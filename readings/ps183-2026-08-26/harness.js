// Same pattern as tests/test_hardware_generation.py::_GPU_READ — execute the
// emitted extension against stubbed contexts and CALL the patched getParameter.
function makeRealm() {
  function WebGLRenderingContext() {}
  function WebGL2RenderingContext() {}
  for (const C of [WebGLRenderingContext, WebGL2RenderingContext]) {
    C.prototype.getParameter = function () { return "HOST_VALUE_NOT_SPOOFED"; };
    C.prototype.getExtension = function () { return null; };
    C.prototype.getSupportedExtensions = function () { return ["HOST_EXT"]; };
    C.prototype.getShaderPrecisionFormat = function () { return null; };
  }
  return { WebGLRenderingContext, WebGL2RenderingContext };
}
const src = require('fs').readFileSync(process.argv[2], 'utf8');
const G = makeRealm();
const sandbox = { self: G, window: G, ...G };
require('vm').createContext(sandbox);
require('vm').runInContext(src, sandbox);
const gl = new G.WebGL2RenderingContext();
console.log(JSON.stringify({
  unmaskedVendor: gl.getParameter(0x9245),
  unmaskedRenderer: gl.getParameter(0x9246),
}));
