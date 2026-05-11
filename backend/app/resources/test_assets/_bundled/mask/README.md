# Mask 测试素材包

测试 `gpt-image-2` (或 OpenAI 中转 / 第三方代理) 的 mask 功能时使用，覆盖 **inpaint** (局部编辑) 和 **outpaint** (画布外扩) 两类任务，每类任务都同时提供 **native** (传 `mask=` 参数) 和 **fallback** (把 mask 作为 `image=[]` 的第二张图) 两种方法的素材。

## 目录结构

```
mask_test_materials/
├── 00_scene_original.png           # 原始场景图 (1536x1024)，inpaint 任务的输入
├── fixtures_manifest.json          # 程序化元数据：bbox 坐标、替换目标等
├── README.md                       # 本文件
│
├── inpaint/                        # 局部编辑素材
│   ├── villager/                   # 目标 A：把村民替换成 creeper
│   │   ├── native_mask.png         #   alpha PNG，传给 `mask=` 参数
│   │   ├── fallback_mask.png       #   B&W PNG，作为 `image=[scene, fallback_mask]` 的第二张图
│   │   └── overlay_review.png      #   人工审核用：高亮显示 mask 区域
│   └── iron_golem/                 # 目标 B：把铁傀儡替换成骷髅
│       ├── native_mask.png
│       ├── fallback_mask.png
│       └── overlay_review.png
│
└── outpaint/                       # 画布外扩素材
    ├── right/                      # 场景 A：向右扩展 768px → 2304x1024
    │   ├── canvas.png              #   扩展画布：左侧原图 + 右侧透明
    │   ├── native_mask.png         #   alpha PNG，传给 `mask=` 参数
    │   ├── fallback_mask.png       #   B&W PNG，作为 `image=[canvas, fallback_mask]` 的第二张图
    │   └── overlay_review.png      #   人工审核用
    └── bottom/                     # 场景 B：向下扩展 512px → 1536x1536
        ├── canvas.png
        ├── native_mask.png
        ├── fallback_mask.png
        └── overlay_review.png
```

## native vs fallback

| 方法 | 调用方式 | 适用场景 |
|---|---|---|
| **native** | `image=scene/canvas, mask=native_mask.png` | 官方 OpenAI · 部分中转支持的标准路径 |
| **fallback** | `image=[scene/canvas, fallback_mask.png]` (不传 `mask=`) | 中转丢弃/破坏 `mask=` 字段时的兜底 |

二者效果一样，区别只在传参方式。

## inpaint 调用范例

### inpaint · native 方法

```python
from openai import OpenAI
client = OpenAI(api_key="...", base_url="...")

result = client.images.edit(
    model="gpt-image-2",
    image=open("00_scene_original.png", "rb"),
    mask=open("inpaint/villager/native_mask.png", "rb"),
    prompt=(
        "Replace the creature that is marked with a green Minecraft creeper. "
        "Keep everything else exactly the same as in the reference image."
    ),
    size="1536x1024",
    quality="medium",
)
```

### inpaint · fallback 方法

```python
result = client.images.edit(
    model="gpt-image-2",
    image=[
        open("00_scene_original.png", "rb"),
        open("inpaint/villager/fallback_mask.png", "rb"),
    ],
    # 注意：不传 mask= 参数
    prompt=(
        "I'm giving you two images. The first image is the scene to edit. "
        "The second image is a black-and-white mask that defines which region to modify: "
        "the white region marks the area to be changed; "
        "the black region must remain unchanged. "
        "Replace the creature located inside the white region of the mask "
        "with a green Minecraft creeper. "
        "Keep everything in the black region exactly the same as in the first image."
    ),
    size="1536x1024",
    quality="medium",
)
```

## outpaint 调用范例

### outpaint · native 方法

```python
result = client.images.edit(
    model="gpt-image-2",
    image=open("outpaint/right/canvas.png", "rb"),
    mask=open("outpaint/right/native_mask.png", "rb"),
    prompt=(
        "In the marked region, render a small Minecraft village in the distance — "
        "wooden and stone Minecraft-style houses with blocky rooftops, and several "
        "villagers near the houses waving cheerfully toward the viewer. "
        "Continue the grass, sky, and blocky clouds smoothly from the reference image. "
        "Keep everything outside the marked region exactly the same as in the reference image."
    ),
    size="2304x1024",       # 必须等于 canvas 实际尺寸
    quality="medium",
)
```

### outpaint · fallback 方法

```python
result = client.images.edit(
    model="gpt-image-2",
    image=[
        open("outpaint/right/canvas.png", "rb"),
        open("outpaint/right/fallback_mask.png", "rb"),
    ],
    # 不传 mask=
    prompt=(
        "I'm giving you two images. The first image is the scene to edit. "
        "The second image is a black-and-white mask that defines which region to fill: "
        "the white region marks the area to be newly generated; "
        "the black region must remain unchanged. "
        "In the white region, render a small Minecraft village in the distance — "
        "wooden and stone Minecraft-style houses with blocky rooftops, and several "
        "villagers near the houses waving cheerfully toward the viewer. "
        "Continue the grass, sky, and blocky clouds smoothly from the first image. "
        "Keep everything in the black region exactly the same as in the first image."
    ),
    size="2304x1024",
    quality="medium",
)
```

## 注意事项

1. **inpaint 的 mask 透明区必须是图内孤岛**，不能延伸到边缘——否则被识别为 outpainting。
2. **outpaint 的 `size` 必须等于 canvas 实际尺寸**，否则模型会缩放原图、对齐错位。
3. **prompt 不要暴露方位/物种**：mask 已经传递了位置信息，prompt 再说"右侧"或"villager"就丧失了对 mask 功能的测试意义。
4. **替换目标 (creeper / skeleton) 和扩展内容 (village / underground cave) 在 manifest.json 里**，业务里用别的替换目标时只需要改 prompt，mask 文件不用动。
