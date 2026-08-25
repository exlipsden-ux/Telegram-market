from dataclasses import dataclass


@dataclass
class Runtime:
    """Значения, которые могут меняться на ходу.

    Адрес витрины живёт здесь, а не в Config: у бесплатного туннеля он
    переезжает при каждом переподключении, и хендлеры должны видеть свежий.
    """

    webapp_url: str = ""

    @property
    def has_webapp(self) -> bool:
        return self.webapp_url.startswith("https://")
