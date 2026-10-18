import threading
import webbrowser

import uvicorn
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from daykeep import config, server


def build_model(settings: config.Settings) -> OpenAIChatModel:
    provider = OpenAIProvider(
        base_url=settings.model.base_url,
        api_key=settings.model.api_key or "not-configured",
    )
    return OpenAIChatModel(settings.model.name, provider=provider)


def main() -> None:
    path = config.default_path()
    settings = config.load(path)
    if not settings.model.api_key:
        print(f"还没有配置 API key，请填写 {path} 里的 api_key 后重启。")
    settings.app.home.mkdir(parents=True, exist_ok=True)
    app = server.create_app(settings, build_model(settings))
    url = f"http://127.0.0.1:{settings.app.port}"
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=settings.app.port)


if __name__ == "__main__":
    main()
