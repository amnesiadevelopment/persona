"""Entry point for `flet build`. Keeps the app code under src/ (with its
`src.*` absolute imports) while giving flet build the canonical root `main`
module it expects."""
from src.main import main

main()
