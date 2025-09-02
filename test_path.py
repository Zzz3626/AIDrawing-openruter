import os
import json

# 读取配置
with open('config.json', 'r', encoding='utf-8') as f:
    config = json.load(f)

out_dir = config.get('storage', {}).get('output_dir') or '/root/LangBot/plugins/AIDrawing-openruter/generated'
print(f"Configured output directory: {out_dir}")

# 生成测试文件路径
filename = "test_image.png"
out_path = os.path.join(out_dir, filename)
print(f"Generated path: {out_path}")

# 检查 abspath 的行为
abs_path = os.path.abspath(out_path)
print(f"After os.path.abspath: {abs_path}")

# 检查目录是否存在
print(f"Directory exists: {os.path.exists(out_dir)}")
print(f"Is absolute: {os.path.isabs(out_path)}")
