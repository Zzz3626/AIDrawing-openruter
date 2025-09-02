from pkg.plugin.context import register, handler, llm_func, BasePlugin, APIHost, EventContext
from pkg.plugin.events import *  # 导入事件
from pkg.platform.types import *
import re
import os
import json
import uuid
import logging
from pathlib import Path
import base64

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

# 兼容不同宿主中事件类名差异：将 Normal* 名称映射到 Person*
try:
    NormalMessageReceived  # type: ignore[name-defined]
except NameError:
    try:
        from pkg.plugin.events import PersonMessageReceived as NormalMessageReceived  # type: ignore
    except Exception:
        pass

try:
    NormalMessageResponded  # type: ignore[name-defined]
except NameError:
    try:
        from pkg.plugin.events import PersonMessageResponded as NormalMessageResponded  # type: ignore
    except Exception:
        pass


# 注册插件
@register(
    name="AIDrawing",
    description="AI image generation via function calling, with image sending",
    version="0.1",
    author="Hanschase",
)
class Fct(BasePlugin):
    def __init__(self, host: APIHost):
        # setup file logger once
        try:
            base_dir_for_log = Path(__file__).parent
        except Exception:
            base_dir_for_log = Path(os.getcwd())
        log_dir = base_dir_for_log / "logs"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            self._logger = logging.getLogger("AIDrawing")
            if not self._logger.handlers:
                fh = logging.FileHandler(log_dir / "aidrawing.log", encoding="utf-8")
                fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
                fh.setFormatter(fmt)
                fh.setLevel(logging.DEBUG)
                self._logger.addHandler(fh)
            self._logger.setLevel(logging.DEBUG)
        except Exception:
            self._logger = logging.getLogger("AIDrawing")
            self._logger.setLevel(logging.DEBUG)

        # 读取配置文件（与本文件同目录）config.json
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
            # Normalize API key after merge: support variants and env
            _open = self.config.get('openrouter', {}) or {}
            def _pick_key(d: dict):
                if not isinstance(d, dict):
                    return None
                for _k in ("api_key", "apikey", "apiKey", "key", "token", "OPENROUTER_API_KEY"):
                    _v = d.get(_k)
                    if isinstance(_v, str) and _v.strip():
                        return _v.strip()
                return None
            _resolved_key = _pick_key(_open) or _pick_key(self.config) or os.getenv('OPENROUTER_API_KEY')
            if _resolved_key:
                # Persist normalized location for downstream usage
                _open['api_key'] = _resolved_key
                self.config['openrouter'] = _open
                if hasattr(self, 'ap') and getattr(self, 'ap', None):
                    self.ap.logger.info(f"OpenRouter API Key 已配置 (len={len(_resolved_key)}). 配置文件: {cfg_path}")
                # also write to file log
                try:
                    self._logger.info(f"API key detected via config/env. len={len(_resolved_key)}; cfg={cfg_path}")
                except Exception:
                    pass
            else:
                if hasattr(self, 'ap') and getattr(self, 'ap', None):
                    self.ap.logger.info(f"未在配置/环境中检测到 OpenRouter API Key。配置文件: {cfg_path}")
                try:
                    self._logger.warning(f"No API key in config/env. cfg={cfg_path}")
                except Exception:
                    pass
        except Exception as e:
            if hasattr(self, 'ap') and getattr(self, 'ap', None):
                self.ap.logger.warning(f"读取配置失败，使用默认配置: {e}")
            try:
                self._logger.exception("Failed to load config.json: %s", e)
            except Exception:
                pass
        # 确保输出目录存在：将相对路径固定到插件目录（与 logs 同级），并记录最终绝对路径
        storage_cfg = self.config.get('storage', {}) or {}
        raw_out_dir = storage_cfg.get('output_dir') or 'generated'
        try:
            # 调试：打印配置加载情况
            self._logger.info(f"Config loaded - storage config: {storage_cfg}")
            self._logger.info(f"Raw output dir from config: {raw_out_dir}")

            try:
                _plugin_dir = os.path.dirname(__file__)
            except Exception:
                _plugin_dir = os.getcwd()
            out_dir = raw_out_dir if os.path.isabs(raw_out_dir) else os.path.join(_plugin_dir, raw_out_dir)
            # 回写标准化后的绝对路径，便于后续调用
            if isinstance(self.config.get('storage'), dict):
                self.config['storage']['output_dir'] = out_dir
            os.makedirs(out_dir, exist_ok=True)
            # 记录配置的路径信息
            self._logger.info(f"Output directory configured: {out_dir}")
            if hasattr(self, 'ap') and getattr(self, 'ap', None):
                self.ap.logger.info(f"图片输出目录已设置为: {out_dir}")
        except Exception as e:
            if hasattr(self, 'ap') and getattr(self, 'ap', None):
                self.ap.logger.warning(f"创建输出目录失败，将使用当前目录: {e}")
            try:
                self._logger.warning("Failed to create output dir '%s': %s", out_dir, e)
            except Exception:
                pass

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
        # 使用在 __init__ 中标准化后的绝对路径；若缺失则退回到当前文件同目录 generated
        configured_dir = cfg.get('storage', {}).get('output_dir')
        if not configured_dir:
            try:
                _plugin_dir = os.path.dirname(__file__)
            except Exception:
                _plugin_dir = os.getcwd()
            configured_dir = os.path.join(_plugin_dir, 'generated')
        out_dir = configured_dir
        
        # 调试信息：打印实际使用的路径
        try:
            self._logger.info(f"Function calling - configured_dir: {configured_dir}, using out_dir: {out_dir}")
            self.ap.logger.info(f"Function calling - 使用输出目录: {out_dir}")
        except Exception:
            pass

        # Helper to robustly extract API key from config/root/env
        def _get_api_key(openrouter_dict, root_cfg=self.config):
            import os as _os
            # 1) check openrouter section
            if isinstance(openrouter_dict, dict):
                for k in ("api_key", "apikey", "apiKey", "key", "token", "OPENROUTER_API_KEY"):
                    v = openrouter_dict.get(k)
                    if isinstance(v, str) and v.strip():
                        return v.strip()
            # 2) check root-level fallbacks
            if isinstance(root_cfg, dict):
                for k in ("openrouter_api_key", "OPENROUTER_API_KEY", "api_key", "apiKey", "apikey", "key", "token"):
                    v = root_cfg.get(k)
                    if isinstance(v, str) and v.strip():
                        return v.strip()
            # 3) environment
            v = _os.getenv("OPENROUTER_API_KEY")
            if isinstance(v, str) and v.strip():
                return v.strip()
            return None

        if openrouter_cfg.get('enabled', True):
            try:
                # 确保输出目录存在
                os.makedirs(out_dir, exist_ok=True)
                filename = f"drawer_{uuid.uuid4().hex}.png"
                out_path = os.path.join(out_dir, filename)
                try:
                    # file log what we will call
                    key_for_call = (_get_api_key(openrouter_cfg) or None)
                    masked = (key_for_call[:4] + '***' + key_for_call[-4:]) if isinstance(key_for_call, str) and len(key_for_call) >= 8 else str(bool(key_for_call))
                    self._logger.info(f"Call generate_image_with_openrouter keywords_len={len(keywords)} model={openrouter_cfg.get('model')} out_path={out_path} api_key={masked}")
                except Exception:
                    pass
                img_path = await generate_image_with_openrouter(
                    keywords,
                    out_path=out_path,
                    site_url=(openrouter_cfg.get('site_url') or None),
                    site_title=(openrouter_cfg.get('site_title') or None),
                    model=openrouter_cfg.get('model', 'google/gemini-2.5-flash-image-preview:free') or 'google/gemini-2.5-flash-image-preview:free',
                    api_key=(_get_api_key(openrouter_cfg) or None),
                )
                # 直接返回文件路径，由消息处理器处理
                return f"图片已生成: {img_path}"
            except Exception as e:
                self.ap.logger.warning(f"OpenRouter 生成失败，准备回退: {e}")
                try:
                    self._logger.warning("OpenRouter failed, will fallback: %s", e)
                except Exception:
                    pass

        # 回退
        if fallback_cfg.get('enabled', True):
            return "https://image.pollinations.ai/prompt/" + keywords
        # 若禁用回退，直接返回错误信息
        return f"生成失败，且已禁用回退"

    # 发送图片
    @handler(NormalMessageResponded)
    async def convert_message(self, ctx: EventContext):
        message = getattr(ctx.event, 'response_text', '') or ''

        # 正则
        image_pattern = re.compile(r'(https://image[^\s)]+)')
        file_pattern = re.compile(r'(file://[^\s)]+)')
        markdown_image_pattern = re.compile(r'!\[[^\]]*\]\(([^)]+)\)')
        generated_image_pattern = re.compile(r'图片已生成:\s*([A-Za-z]:\\[^\n\r]*?\.(?:png|jpg|jpeg|gif|webp)|/[^\n\r]*?\.(?:png|jpg|jpeg|gif|webp))', re.IGNORECASE)

        def _sanitize_path(p: str) -> str:
            p = (p or '').strip().strip('"').strip("'")
            return os.path.abspath(p)

        # 1) “图片已生成: 本地路径”
        m = generated_image_pattern.search(message)
        if m:
            path = _sanitize_path(m.group(1))
            try:
                self.ap.logger.info(f"检测到生成的图片，正在发送.. {path}")
                if os.path.exists(path):
                    ctx.add_return('reply', MessageChain([Image(path=path)]))
                else:
                    ctx.add_return('reply', MessageChain([Plain(f"图片文件不存在: {path}")]))
            except Exception as e:
                await ctx.send_message(ctx.event.launcher_type, str(ctx.event.launcher_id), MessageChain([f"发生了一个错误：{e}"]))
            return

        # 2) Markdown 本地图片
        m = markdown_image_pattern.search(message)
        if m:
            path = _sanitize_path(m.group(1))
            try:
                self.ap.logger.info(f"正在发送本地图片.. {path}")
                if os.path.exists(path):
                    ctx.add_return('reply', MessageChain([Image(path=path)]))
                else:
                    ctx.add_return('reply', MessageChain([Plain(f"图片文件不存在: {path}")]))
            except Exception as e:
                await ctx.send_message(ctx.event.launcher_type, str(ctx.event.launcher_id), MessageChain([f"发生了一个错误：{e}"]))
            return

        # 3) 远程图片 URL
        m = image_pattern.search(message)
        if m:
            url = m.group(1)
            try:
                if url.endswith('.') or url.endswith(')'):
                    url = url[:-1]
                self.ap.logger.info(f"正在发送图片.. {url}")
                ctx.add_return('reply', MessageChain([Image(url=url)]))
            except Exception as e:
                await ctx.send_message(ctx.event.launcher_type, str(ctx.event.launcher_id), MessageChain([f"发生了一个错误：{e}"]))
            return

        # 4) file:// URL -> 转成本地路径
        m = file_pattern.search(message)
        if m:
            file_url = (m.group(1) or '').strip()
            path = file_url[7:] if file_url.startswith('file://') else file_url
            path = _sanitize_path(path)
            try:
                self.ap.logger.info(f"正在发送本地图片.. {path}")
                if os.path.exists(path):
                    ctx.add_return('reply', MessageChain([Image(path=path)]))
                else:
                    ctx.add_return('reply', MessageChain([Plain(f"图片文件不存在: {path}")]))
            except Exception as e:
                await ctx.send_message(ctx.event.launcher_type, str(ctx.event.launcher_id), MessageChain([f"发生了一个错误：{e}"]))
            return

        # 5) 默认：直接回传文本
        return ctx.add_return('reply', message)

    def __del__(self):
        pass

    # 解析 /p 指令并直接触发生图（不经过 function calling）
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
            return ctx.add_return('reply', MessageChain([Plain('请输入绘图描述，例如 /p 一只在月球上的猫')]))

        cfg = self.config
        openrouter_cfg = cfg.get('openrouter', {})
        fallback_cfg = cfg.get('fallback', {})
        # 使用在 __init__ 中标准化后的绝对路径；若缺失则退回到当前文件同目录 generated
        configured_dir = cfg.get('storage', {}).get('output_dir')
        if not configured_dir:
            try:
                _plugin_dir = os.path.dirname(__file__)
            except Exception:
                _plugin_dir = os.getcwd()
            configured_dir = os.path.join(_plugin_dir, 'generated')
        out_dir = configured_dir

        # 调试信息：打印实际使用的路径
        try:
            self._logger.info(f"Direct command - configured_dir: {configured_dir}, using out_dir: {out_dir}")
            self.ap.logger.info(f"Direct command - 使用输出目录: {out_dir}")
        except Exception:
            pass

        # Helper to robustly extract API key from config/root/env
        def _get_api_key(openrouter_dict, root_cfg=self.config):
            import os as _os
            # 1) check openrouter section
            if isinstance(openrouter_dict, dict):
                for k in ("api_key", "apikey", "apiKey", "key", "token", "OPENROUTER_API_KEY"):
                    v = openrouter_dict.get(k)
                    if isinstance(v, str) and v.strip():
                        return v.strip()
            # 2) check root-level fallbacks
            if isinstance(root_cfg, dict):
                for k in ("openrouter_api_key", "OPENROUTER_API_KEY", "api_key", "apiKey", "apikey", "key", "token"):
                    v = root_cfg.get(k)
                    if isinstance(v, str) and v.strip():
                        return v.strip()
            # 3) environment
            v = _os.getenv("OPENROUTER_API_KEY")
            if isinstance(v, str) and v.strip():
                return v.strip()
            return None

        if openrouter_cfg.get('enabled', True):
            try:
                # 确保输出目录存在
                os.makedirs(out_dir, exist_ok=True)
                filename = f"drawer_{uuid.uuid4().hex}.png"
                out_path = os.path.join(out_dir, filename)
                try:
                    key_for_call = (_get_api_key(openrouter_cfg) or None)
                    masked = (key_for_call[:4] + '***' + key_for_call[-4:]) if isinstance(key_for_call, str) and len(key_for_call) >= 8 else str(bool(key_for_call))
                    self._logger.info(f"Call generate_image_with_openrouter prompt_len={len(prompt)} model={openrouter_cfg.get('model')} out_path={out_path} api_key={masked}")
                except Exception:
                    pass
                img_path = await generate_image_with_openrouter(
                    prompt,
                    out_path=out_path,
                    site_url=(openrouter_cfg.get('site_url') or None),
                    site_title=(openrouter_cfg.get('site_title') or None),
                    model=openrouter_cfg.get('model', 'google/gemini-2.5-flash-image-preview:free') or 'google/gemini-2.5-flash-image-preview:free',
                    api_key=(_get_api_key(openrouter_cfg) or None),
                )
                self.ap.logger.info(f"{prefix} 生成完成，发送本地图片: {img_path}")
                # 直接以本地文件路径发送，避免 URL 校验与长度限制
                return ctx.add_return('reply', MessageChain([Image(path=os.path.abspath(img_path))]))
            except Exception as e:
                self.ap.logger.warning(f"OpenRouter 生成失败，准备回退: {e}")
                try:
                    self._logger.warning("OpenRouter failed, will fallback: %s", e)
                except Exception:
                    pass

        if fallback_cfg.get('enabled', True):
            url = "https://image.pollinations.ai/prompt/" + prompt
            return ctx.add_return('reply', MessageChain([Image(url=url)]))
        else:
            return ctx.add_return('reply', MessageChain([Plain('生成失败，且已禁用回退')]))
