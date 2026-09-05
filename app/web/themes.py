from __future__ import annotations

from dataclasses import dataclass

from fastapi.templating import Jinja2Templates


@dataclass(frozen=True)
class WebTheme:
    id: str
    name: str
    description: str
    color_scheme: str


@dataclass(frozen=True)
class WebFontSize:
    id: str
    name: str
    description: str
    scale: float


DEFAULT_THEME_ID = "standard"
WEB_THEMES = (
    WebTheme(
        id="standard",
        name="Standard",
        description="亮色主题",
        color_scheme="light",
    ),
    WebTheme(
        id="code-dark",
        name="Code Dark",
        description="暗色主题",
        color_scheme="dark",
    ),
    WebTheme(
        id="studio-cyan",
        name="Studio Cyan",
        description="冷静浅色主题",
        color_scheme="light",
    ),
)
_THEMES_BY_ID = {theme.id: theme for theme in WEB_THEMES}
if (
    not WEB_THEMES
    or len(_THEMES_BY_ID) != len(WEB_THEMES)
    or DEFAULT_THEME_ID not in _THEMES_BY_ID
    or any(
        not theme.id
        or not theme.name
        or not theme.description
        or theme.color_scheme not in {"light", "dark"}
        for theme in WEB_THEMES
    )
):
    raise RuntimeError("Web theme registry is invalid")

THEME_SCHEMES = ",".join(
    f"{theme.id}:{theme.color_scheme}" for theme in WEB_THEMES
)

DEFAULT_FONT_SIZE_ID = "default"
WEB_FONT_SIZES = (
    WebFontSize(id="small", name="小", description="90%", scale=0.9),
    WebFontSize(id="default", name="默认", description="100%", scale=1.0),
    WebFontSize(id="large", name="大", description="110%", scale=1.1),
)
_FONT_SIZES_BY_ID = {font_size.id: font_size for font_size in WEB_FONT_SIZES}
if (
    not WEB_FONT_SIZES
    or len(_FONT_SIZES_BY_ID) != len(WEB_FONT_SIZES)
    or DEFAULT_FONT_SIZE_ID not in _FONT_SIZES_BY_ID
    or any(
        not font_size.id
        or not font_size.name
        or not font_size.description
        or font_size.scale <= 0
        for font_size in WEB_FONT_SIZES
    )
):
    raise RuntimeError("Web font size registry is invalid")

FONT_SIZE_SCALES = ",".join(
    f"{font_size.id}:{font_size.scale:g}" for font_size in WEB_FONT_SIZES
)


def resolve_web_theme(theme_id: str | None) -> WebTheme:
    return _THEMES_BY_ID.get(theme_id or "", _THEMES_BY_ID[DEFAULT_THEME_ID])


def resolve_web_font_size(font_size_id: str | None) -> WebFontSize:
    return _FONT_SIZES_BY_ID.get(
        font_size_id or "",
        _FONT_SIZES_BY_ID[DEFAULT_FONT_SIZE_ID],
    )


def configure_theme_templates(templates: Jinja2Templates) -> None:
    templates.env.globals.update(
        default_ui_theme=DEFAULT_THEME_ID,
        resolve_ui_theme=resolve_web_theme,
        ui_theme_schemes=THEME_SCHEMES,
        web_themes=WEB_THEMES,
        default_ui_font_size=DEFAULT_FONT_SIZE_ID,
        resolve_ui_font_size=resolve_web_font_size,
        ui_font_size_scales=FONT_SIZE_SCALES,
        web_font_sizes=WEB_FONT_SIZES,
    )
