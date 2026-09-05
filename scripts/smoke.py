"""Verify the installed application and its HTTP/OpenAPI contract without providers."""

from fastapi.testclient import TestClient

from papertrail.main import app


def main() -> None:
    with TestClient(app) as client:
        health = client.get("/health")
        health.raise_for_status()
        if health.json() != {"status": "ok"}:
            raise RuntimeError("Unexpected health response")

        schema = client.get("/openapi.json")
        schema.raise_for_status()
        if "/health" not in schema.json()["paths"]:
            raise RuntimeError("Health endpoint is missing from OpenAPI")

        client.get("/docs").raise_for_status()

    print("PASS: installed package, health response, OpenAPI schema and docs route")


if __name__ == "__main__":
    main()
