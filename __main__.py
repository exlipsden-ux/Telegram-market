from .main import main

if __name__ == "__main__":
    import asyncio

    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit) as exc:
        if str(exc):
            print(exc)
