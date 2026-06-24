"""Trigger Flet to download its desktop (Flutter) client into ~/.flet/client so
persona.spec can bundle it. Run headlessly in CI. Exits quickly either way."""
import flet


def main(page):
    page.window.close()


if __name__ == "__main__":
    try:
        flet.app(target=main)
    except Exception as e:  # noqa: BLE001
        print("flet prefetch finished:", e)
