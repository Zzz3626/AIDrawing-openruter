from pkg.plugin.context import register, handler, llm_func, BasePlugin, APIHost, EventContext
from pkg.plugin.events import *  # 导入事件类
from pkg.platform.types import *
import re
import os
import json
import uuid
# Prefer local get_image within this plugin; fall back gracefully
try:
    # Relative import when package context is available
    from .get_image import generate_image_with_openrouter  # type: ignore
except Exception:
    try:
        # Direct import if executed as a flat module
        from get_image import generate_image_with_openrouter  # type: ignore
    except Exception:
        # Last resort: load by path to handle non-standard plugin loaders
        import importlib.util
        import pathlib
        _base_dir = pathlib.Path(__file__).parent
        _spec = importlib.util.spec_from_file_location("get_image", _base_dir / "get_image.py")
        if _spec and _spec.loader:
            _mod = importlib.util.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)  # type: ignore
            generate_image_with_openrouter = _mod.generate_image_with_openrouter  # type: ignore
        else:
            raise ImportError("Cannot load local get_image.py")


# 注册插件
@register(name="AIDrawing", description="使用function calling函数实现AI画图的功能，并自带图像发送", version="0.1", author="Hanschase")
class Fct(BasePlugin):
    def __init__(self, host: APIHost):
        # 读取配置文件（与本文件同目录的 config.json）
        try:
            base_dir = os.path.dirname(__file__)
        except Exception:
            base_dir = os.getcwd()
        cfg_path = os.path.join(base_dir, 'config.json')
        self.config = {
            "command_prefix": "/p",
            "openrouter": {
                "enabled": True,
                "model": "google/gemini-2.5-flash-image-preview:free",
                "api_key": "",
                "site_url": "",
                "site_title": "",
            },
            "storage": {"output_dir": "generated"},
            "fallback": {"enabled": True, "provider": "pollinations"},
        }
        try:
            if os.path.exists(cfg_path):
                with open(cfg_path, 'r', encoding='utf-8') as f:
                    # 浅合并配置
                    user_cfg = json.load(f)
                    def merge(dst, src):
                        for k, v in src.items():
                            if isinstance(v, dict) and isinstance(dst.get(k), dict):
                                merge(dst[k], v)
                            else:
                                dst[k] = v
                    merge(self.config, user_cfg)
        except Exception as e:
            if hasattr(self, 'ap') and getattr(self, 'ap', None):
                self.ap.logger.warning(f"读取配置失败，使用默认配置: {e}")
        # 确保输出目录存在
        out_dir = self.config.get('storage', {}).get('output_dir') or 'generated'
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception as e:
            if hasattr(self, 'ap') and getattr(self, 'ap', None):
                self.ap.logger.warning(f"创建输出目录失败，将使用当前目录: {e}")

    @llm_func(name="Drawer")
    async def _(self,query, keywords: str)->str:
        """Call this function to draw something before you answer any questions.
        - Expand the user's description into a more elaborate and detailed English prompt suitable for AI image generation, including adding details like camera aperture and specific scene descriptions, and then input the enhanced description.
        - You will be will become reticent.
        - Use the English keywords.
        - Try to use short keywords as much as possible

        Args:
            keywords: The enhanced description.

        Returns:
            img: The generated image.
        """
        self.ap.logger.info(f"优化后关键词,{keywords}")
        cfg = self.config
        openrouter_cfg = cfg.get('openrouter', {})
        fallback_cfg = cfg.get('fallback', {})
        out_dir = cfg.get('storage', {}).get('output_dir') or 'generated'

        if openrouter_cfg.get('enabled', True):
            try:
                filename = f"drawer_{uuid.uuid4().hex}.png"
                out_path = os.path.join(out_dir, filename)
                img_path = await generate_image_with_openrouter(
                    keywords,
                    out_path=out_path,
                    site_url=(openrouter_cfg.get('site_url') or None),
                    site_title=(openrouter_cfg.get('site_title') or None),
                    model=openrouter_cfg.get('model', 'google/gemini-2.5-flash-image-preview:free') or 'google/gemini-2.5-flash-image-preview:free',
                    api_key=(openrouter_cfg.get('api_key') or None),
                )
                return f"file://{img_path}"
            except Exception as e:
                self.ap.logger.warning(f"OpenRouter 生成失败，准备回退: {e}")

        # 回退
        if fallback_cfg.get('enabled', True):
            return "https://image.pollinations.ai/prompt/" + keywords
        # 若禁用回退，直接返回错误信息
        return f"生成失败，且已禁用回退。"

    #发送图片
    @handler(NormalMessageResponded)
    async def convert_message(self, ctx: EventContext):
        message = ctx.event.response_text
        image_pattern = re.compile(r'(https://image[^\s)]+)')
        file_pattern = re.compile(r'(file://[^\s)]+)')
        #如果匹配到了image_pattern
        if image_pattern.search(message):
            url = image_pattern.search(message).group(1)
            try:
                #去除url末尾的句号或者括号
                if url.endswith('.') or url.endswith(')'):
                    url = url[:-1]
                self.ap.logger.info(f"正在发送图片图片...{url}")
                ctx.add_return('reply', MessageChain([Image(url=url)]))
            except Exception as e:
                await ctx.send_message(ctx.event.launcher_type, str(ctx.event.launcher_id),MessageChain([f"发生了一个错误：{e}"]))
        elif file_pattern.search(message):
            file_url = file_pattern.search(message).group(1)
            # Strip file:// prefix
            path = file_url.replace('file://', '')
            try:
                self.ap.logger.info(f"正在发送本地图片...{path}")
                ctx.add_return('reply', MessageChain([Image(path=path)]))
            except Exception as e:
                await ctx.send_message(ctx.event.launcher_type, str(ctx.event.launcher_id),MessageChain([f"发生了一个错误：{e}"]))
        else:
            return ctx.add_return('reply', message)

    def __del__(self):
        pass

    # 解析 /p 指令并直接触发生图（不经由 function calling）
    @handler(NormalMessageReceived)
    async def handle_prompt_command(self, ctx: EventContext):
        # 尝试从多种字段中获取文本，兼容不同平台事件结构
        text = getattr(ctx.event, 'text', None) \
               or getattr(ctx.event, 'message', None) \
               or getattr(ctx.event, 'text_message', None) \
               or getattr(ctx.event, 'message_text', None) \
               or ''

        if not isinstance(text, str):
            try:
                text = str(text)
            except Exception:
                text = ''

        content = text.strip()
        if not content:
            return

        # 匹配配置中的指令前缀，例如 /p
        # 支持空格、冒号、中英文标点
        prefix = self.config.get('command_prefix', '/p') or '/p'
        # 正则转义用户自定义前缀
        import re as _re
        safe_prefix = _re.escape(prefix)
        m = re.match(fr"^{safe_prefix}\s*[:：]?\s*(.+)$", content, flags=re.IGNORECASE)
        if not m:
            return

        prompt = m.group(1).strip()
        if not prompt:
            return ctx.add_return('reply', MessageChain([Plain('请输入绘图描述，例如：/p 一只在月球上的猫')]))

        cfg = self.config
        openrouter_cfg = cfg.get('openrouter', {})
        fallback_cfg = cfg.get('fallback', {})
        out_dir = cfg.get('storage', {}).get('output_dir') or 'generated'

        if openrouter_cfg.get('enabled', True):
            try:
                filename = f"drawer_{uuid.uuid4().hex}.png"
                out_path = os.path.join(out_dir, filename)
                img_path = await generate_image_with_openrouter(
                    prompt,
                    out_path=out_path,
                    site_url=(openrouter_cfg.get('site_url') or None),
                    site_title=(openrouter_cfg.get('site_title') or None),
                    model=openrouter_cfg.get('model', 'google/gemini-2.5-flash-image-preview:free') or 'google/gemini-2.5-flash-image-preview:free',
                    api_key=(openrouter_cfg.get('api_key') or None),
                )
                self.ap.logger.info(f"{prefix} 生成完成，发送本地图片: {img_path}")
                return ctx.add_return('reply', MessageChain([Image(path=img_path)]))
            except Exception as e:
                self.ap.logger.warning(f"OpenRouter 生成失败，准备回退: {e}")

        if fallback_cfg.get('enabled', True):
            url = "https://image.pollinations.ai/prompt/" + prompt
            return ctx.add_return('reply', MessageChain([Image(url=url)]))
        else:
            return ctx.add_return('reply', MessageChain([Plain('生成失败，且已禁用回退。')]))
