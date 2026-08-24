from app.model_contract import require_models


def main() -> None:
    require_models()
    print("required local models ready")


if __name__ == "__main__":
    main()
