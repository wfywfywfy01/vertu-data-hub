"""Start the private knowledge API with a psycopg-compatible Windows loop."""
import os
import sys

import uvicorn


def main() -> None:
    uvicorn.run(
        "app.api.main:app",
        host=os.getenv("API_HOST", "127.0.0.1"),
        port=int(os.getenv("API_PORT", "8080")),
        loop="app.runtime:selector_loop_factory" if sys.platform == "win32" else "auto",
    )


if __name__ == "__main__":
    main()
