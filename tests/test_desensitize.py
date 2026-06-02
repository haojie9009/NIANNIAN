"""测试脱敏函数"""
import sys
sys.path.insert(0, 'backend')

from services.service_manager import _desensitize_name_in_prompt

# 测试用例
test_cases = [
    {
        "name": "张雪峰",
        "form_data": {"deceased_name": "张雪峰", "speaker_relation": "儿子"},
        "prompt": "镜头从张雪峰的左侧缓缓横移，渐渐停留在正面特写。"
    },
    {
        "name": "张雪峰",
        "form_data": {"deceased_name": "张雪峰", "speaker_relation": "女儿"},
        "prompt": "他端坐在直播间桌前，耐心地对镜头说话。"
    },
    {
        "name": "张先生",
        "form_data": {"deceased_name": "张先生"},
        "prompt": "张先生坐在那里。"
    },
    {
        "name": "张女士",
        "form_data": {"deceased_name": "张女士"},
        "prompt": "张女士微笑点头。"
    },
]

print("=" * 60)
print("测试脱敏函数 _desensitize_name_in_prompt")
print("=" * 60)

for i, tc in enumerate(test_cases, 1):
    name = tc["name"]
    form_data = tc["form_data"]
    prompt = tc["prompt"]

    result = _desensitize_name_in_prompt(prompt, form_data)

    print(f"\n测试 {i}: name='{name}'")
    print(f"  原 prompt: {prompt}")
    print(f"  处理后:   {result}")

    # 检查是否包含原名（脱敏失败）
    if name in result:
        print(f"  ⚠️  警告: 脱敏失败，原名仍在prompt中!")
    else:
        print(f"  ✅ 脱敏成功")

print("\n" + "=" * 60)