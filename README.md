# HelloPlugin

<!--
## 插件开发者详阅

### 开始

此仓库是 LangBot 插件模板，您可以直接在 GitHub 仓库中点击右上角的 "Use this template" 以创建你的插件。  
接下来按照以下步骤修改模板代码：

#### 修改模板代码

- 修改此文档顶部插件名称信息
- 将此文档下方的`<插件发布仓库地址>`改为你的插件在 GitHub· 上的地址
- 补充下方的`使用`章节内容
- 修改`main.py`中的`@register`中的插件 名称、描述、版本、作者 等信息
- 修改`main.py`中的`MyPlugin`类名为你的插件类名
- 将插件所需依赖库写到`requirements.txt`中
- 根据[插件开发教程](https://docs.langbot.app/plugin/dev/tutor.html)编写插件代码
- 删除 README.md 中的注释内容


#### 发布插件

推荐将插件上传到 GitHub 代码仓库，以便用户通过下方方式安装。   
欢迎[提issue](https://github.com/RockChinQ/LangBot/issues/new?assignees=&labels=%E7%8B%AC%E7%AB%8B%E6%8F%92%E4%BB%B6&projects=&template=submit-plugin.yml&title=%5BPlugin%5D%3A+%E8%AF%B7%E6%B1%82%E7%99%BB%E8%AE%B0%E6%96%B0%E6%8F%92%E4%BB%B6)，将您的插件提交到[插件列表](https://github.com/stars/RockChinQ/lists/qchatgpt-%E6%8F%92%E4%BB%B6)

下方是给用户看的内容，按需修改
-->

## 安装

配置完成 [LangBot](https://github.com/RockChinQ/LangBot) 主程序后使用管理员账号向机器人发送命令即可安装：

```
!plugin get https://github.com/Hanschase/AIDrawing
```
或查看详细的[插件安装说明](https://docs.langbot.app/plugin/plugin-intro.html#%E6%8F%92%E4%BB%B6%E7%94%A8%E6%B3%95)

## 使用

由于主Bot模型不是 Gemini 2.5 Flash Image，本插件通过指令触发而非 function calling。使用方式如下：

1. 发送指令生成图片：
   - `/p <你的绘图描述>`
   - 例如：`/p 一只穿宇航服在月球上的橘猫，写实风格，4k`
2. 插件会调用 OpenRouter 的 `google/gemini-2.5-flash-image-preview:free` 生成图片，并自动发送结果。
3. 若 OpenRouter 绘图失败，将回退到 `pollinations` 的在线生图服务。

提示：
- 为了更稳定的效果，可直接用简短英文关键词描述；也可中文，插件会尽量兼容。
- 生成图片会保存为临时文件并直接发送（无需手动下载）。

## 配置

1. 安装依赖：
   - `pip install -r AIDrawing-master/requirements.txt`
2. 配置文件：`AIDrawing-master/config.json`
   - 可拷贝 `config.example.json` 为 `config.json` 并按需修改。
   - 配置项示例：
     - `command_prefix`: 触发指令前缀（默认 `/p`）
     - `openrouter.enabled`: 是否启用 OpenRouter 生图
     - `openrouter.model`: 使用的模型（默认 `google/gemini-2.5-flash-image-preview:free`）
     - 已移除 `size` 配置：Gemini 图像接口不支持尺寸参数
     - `openrouter.api_key`: 可在此填写 API Key（若不填，读取环境变量 `OPENROUTER_API_KEY`）
     - `openrouter.site_url`/`openrouter.site_title`: 可选，用于 OpenRouter 排名统计头
     - `storage.output_dir`: 生成图片保存目录（默认 `generated`）
     - `fallback.enabled`: 启用失败回退（默认 `true`）
     - `fallback.provider`: 回退提供方（当前支持 `pollinations`）
3. 可选：设置环境变量 API Key（当 `config.json` 未设置时使用）：
   - PowerShell: `$env:OPENROUTER_API_KEY = "sk-or-..."`

## 工作原理

- `/p` 指令触发插件绘图逻辑。
- 首选通过 OpenRouter Images API 生成图片；若不支持则退化为 Chat Completions 提取图片数据/链接；最后兜底到 pollinations。
- 生成的本地图片以 `file://` 形式返回并由插件自动发送。

## 故障排查

- 报错“OPENROUTER_API_KEY is not set”：请先设置环境变量。
- 返回文本而非图片：可能是模型未返回图片数据或网络受限，稍后重试或更换描述。
- 需要切换为仅 pollinations：暂不提供开关，可按需改用旧版逻辑。
