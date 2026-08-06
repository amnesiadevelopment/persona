"""Import and trust a CA certificate in a Firefox cert9.db — using the engine's
own NSS library via ctypes, so no external certutil binary is bundled or needed
on any OS.

This mirrors ``certutil -A -n <nick> -t "CT,C,C" -i ca -d sql:<profile>``: it
initialises NSS against the profile directory, imports the CA, and marks it a
trusted TLS CA. Used to make Firefox accept the mTLS terminator's leaf — only the
terminator's own CA, only in that profile, the OS trust store untouched.
"""

from __future__ import annotations

import ctypes
import os
import threading

from ...core import platform as _platform
from ...core.logging import get_logger

logger = get_logger("cert.nssdb")

# NSS trust-flag bits (certdb.h): a certificate trusted to issue TLS server certs.
_CERTDB_TRUSTED_CA = 0x08
_CERTDB_VALID_CA = 0x10

_SEC_SUCCESS = 0

# NSS init/shutdown is process-global; on macOS trust_ca runs in-process
# (the launch child is a thread), so serialise it and never overlap two imports.
_nss_lock = threading.Lock()


class _CERTCertTrust(ctypes.Structure):
    _fields_ = [
        ("sslFlags", ctypes.c_uint),
        ("emailFlags", ctypes.c_uint),
        ("objectSigningFlags", ctypes.c_uint),
    ]


def _load_nss(engine_dir: str):
    """Load the engine's NSS library. On Windows nss3.dll is self-contained; on
    Linux/macOS NSS is split, so the dependency set is loaded RTLD_GLOBAL first so
    every symbol (e.g. CERT_DecodeCertFromPackage, which lives in smime3)
    resolves."""
    if _platform.IS_WINDOWS:
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(engine_dir)
        return ctypes.CDLL(os.path.join(engine_dir, "nss3.dll"))

    ext = "dylib" if _platform.IS_MACOS else "so"
    if _platform.IS_MACOS:
        # macOS Firefox consolidates NSS into libnss3.dylib (it exports every
        # symbol we need, incl. CERT_DecodeCertFromPackage — unlike Linux where
        # smime3 carries it). libnss3 depends on libmozglue via @rpath, which the
        # loader can't resolve outside the .app, so preload libmozglue (and its
        # NSS siblings if present) RTLD_GLOBAL first, then return libnss3.
        lib = None
        for name in ("libmozglue.dylib", "libnssutil3.dylib", "libnss3.dylib"):
            path = os.path.join(engine_dir, name)
            if os.path.isfile(path):
                lib = ctypes.CDLL(path, mode=ctypes.RTLD_GLOBAL)
        return lib
    ordered = [
        f"libnspr4.{ext}", f"libplc4.{ext}", f"libplds4.{ext}",
        f"libnssutil3.{ext}", f"libnss3.{ext}", f"libssl3.{ext}",
        f"libsmime3.{ext}",
    ]
    lib = None
    for name in ordered:
        path = os.path.join(engine_dir, name)
        if os.path.isfile(path):
            lib = ctypes.CDLL(path, mode=ctypes.RTLD_GLOBAL)
    # smime3 carries CERT_DecodeCertFromPackage and depends on the rest.
    smime = os.path.join(engine_dir, f"libsmime3.{ext}")
    if os.path.isfile(smime):
        return ctypes.CDLL(smime, mode=ctypes.RTLD_GLOBAL)
    return lib


def _bind(nss) -> None:
    sigs = [
        ("NSS_Initialize", ctypes.c_int, [ctypes.c_char_p] * 4 + [ctypes.c_uint]),
        ("NSS_Shutdown", ctypes.c_int, []),
        ("CERT_GetDefaultCertDB", ctypes.c_void_p, []),
        ("CERT_DecodeCertFromPackage", ctypes.c_void_p, [ctypes.c_char_p, ctypes.c_int]),
        ("PK11_GetInternalKeySlot", ctypes.c_void_p, []),
        ("PK11_NeedUserInit", ctypes.c_int, [ctypes.c_void_p]),
        ("PK11_InitPin", ctypes.c_int, [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]),
        ("PK11_Authenticate", ctypes.c_int, [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]),
        ("PK11_ImportCert", ctypes.c_int,
         [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_char_p, ctypes.c_int]),
        ("CERT_FindCertByNickname", ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_char_p]),
        ("CERT_DecodeTrustString", ctypes.c_int,
         [ctypes.POINTER(_CERTCertTrust), ctypes.c_char_p]),
        ("CERT_ChangeCertTrust", ctypes.c_int,
         [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(_CERTCertTrust)]),
        ("CERT_DestroyCertificate", None, [ctypes.c_void_p]),
        ("PK11_FreeSlot", None, [ctypes.c_void_p]),
    ]
    for name, res, args in sigs:
        fn = getattr(nss, name)
        fn.restype = res
        fn.argtypes = args


def _read_der(ca_path: str) -> bytes:
    """Return the CA certificate as DER. Accepts a PEM or DER file."""
    with open(ca_path, "rb") as f:
        raw = f.read()
    if raw.lstrip().startswith(b"-----BEGIN"):
        import base64
        import re

        m = re.search(
            rb"-----BEGIN CERTIFICATE-----(.+?)-----END CERTIFICATE-----",
            raw, re.DOTALL,
        )
        if not m:
            raise ValueError("no PEM certificate block in %r" % ca_path)
        return base64.b64decode(b"".join(m.group(1).split()))
    return raw


def trust_ca(
    profile_dir: str,
    ca_path: str,
    engine_dir: str,
    nickname: str = "persona-mtls-terminator",
) -> bool:
    """Import ``ca_path`` into ``profile_dir``'s cert9.db as a trusted TLS CA.
    Returns True on success, False on any failure (the caller then launches
    without the trust rather than crashing)."""
    if not ca_path or not os.path.isfile(ca_path):
        return False
    if not engine_dir or not os.path.isdir(engine_dir):
        logger.error("engine dir missing (%s); cannot trust CA", engine_dir)
        return False
    try:
        der = _read_der(ca_path)
        nss = _load_nss(engine_dir)
        if nss is None:
            logger.error("could not load NSS from %s", engine_dir)
            return False
        _bind(nss)
    except Exception as e:
        logger.exception("NSS load/bind failed: %s", e)
        return False

    with _nss_lock:
      try:
        if nss.NSS_Initialize(
            f"sql:{profile_dir}".encode(), b"", b"", b"secmod.db", 0
        ) != _SEC_SUCCESS:
            logger.error("NSS_Initialize failed for %s", profile_dir)
            return False
        slot = cert = perm = vcert = None
        try:
            slot = nss.PK11_GetInternalKeySlot()
            # A fresh sql db has an uninitialised PIN; certutil sets it empty
            # before writing trust, otherwise the trust change is refused.
            if nss.PK11_NeedUserInit(slot):
                nss.PK11_InitPin(slot, None, b"")
            nss.PK11_Authenticate(slot, 1, None)

            buf = (ctypes.c_char * len(der)).from_buffer_copy(der)
            cert = nss.CERT_DecodeCertFromPackage(buf, len(der))
            if not cert:
                logger.error("CERT_DecodeCertFromPackage failed")
                return False
            nss.PK11_ImportCert(slot, cert, 0, nickname.encode(), 0)

            db = nss.CERT_GetDefaultCertDB()
            perm = nss.CERT_FindCertByNickname(db, nickname.encode())
            trust = _CERTCertTrust()
            nss.CERT_DecodeTrustString(ctypes.byref(trust), b"CT,C,C")
            if nss.CERT_ChangeCertTrust(
                db, perm or cert, ctypes.byref(trust)
            ) != _SEC_SUCCESS:
                logger.error("CERT_ChangeCertTrust failed")
                return False

            # confirm the trust bits landed
            vcert = nss.CERT_FindCertByNickname(db, nickname.encode())
            if not vcert:
                return False
            got = _CERTCertTrust()
            nss.CERT_GetCertTrust = getattr(nss, "CERT_GetCertTrust")
            nss.CERT_GetCertTrust.argtypes = [
                ctypes.c_void_p, ctypes.POINTER(_CERTCertTrust)
            ]
            nss.CERT_GetCertTrust(vcert, ctypes.byref(got))
            return bool(got.sslFlags & _CERTDB_TRUSTED_CA) and bool(
                got.sslFlags & _CERTDB_VALID_CA
            )
        finally:
            # Release every handle BEFORE shutdown, else NSS_Shutdown returns
            # SEC_ERROR_BUSY and NSS stays initialised bound to THIS profile's
            # db — the next profile's import then silently launches without CA
            # trust (macOS runs this in-process across profiles).
            for _c in (cert, perm, vcert):
                if _c:
                    try:
                        nss.CERT_DestroyCertificate(_c)
                    except Exception:
                        pass
            if slot:
                try:
                    nss.PK11_FreeSlot(slot)
                except Exception:
                    pass
            if nss.NSS_Shutdown() != _SEC_SUCCESS:
                logger.error("NSS_Shutdown failed — NSS may stay initialised")
      except Exception as e:
        logger.exception("NSS CA import failed: %s", e)
        return False
