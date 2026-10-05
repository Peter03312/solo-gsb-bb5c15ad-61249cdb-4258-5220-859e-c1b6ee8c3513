# ChemMix Precheck — 化学试剂混配风险预检 API

提交容器初始液体、按顺序执行的加液步骤（含试剂浓度与体积）、容器材质与温度，
系统逐步模拟混配过程、估算各组分浓度，并依据公开化学反应规则识别：

- **禁配组合**（`incompatible_pair`）：如酸 + 次氯酸盐释放氯气、酸 + 氰化物释放 HCN 等；
- **超时接触**（`contact_timeout`）：对声明了允许接触时长的反应对，累计接触时间超限即阻断；
- **材质不相容**（`material_incompatibility`）：如氢氟酸腐蚀玻璃、强碱腐蚀铝、有机溶剂溶解 PS、超材质耐温上限等。

输出**首次触发步骤**、涉及组分、**估算浓度**（mol/L）与阻断原因；稀释、倒出、清洗会改变后续模拟状态。
不合法的步骤（序号重复、体积非正、浓度越界、未知试剂、清洗后引用旧状态等）会被**逐项拒绝**（HTTP 422，逐条列出）。

> ⚠️ **安全声明**：本服务仅为混配方案的预检辅助工具，规则与浓度均为近似估算，
> 不能替代化学品安全技术说明书（SDS）、实验室安全规程与专业人员判断。

---

## 快速开始

### Docker Compose（推荐）

```bash
docker compose up --build
# 服务监听 http://127.0.0.1:8000  （交互式文档: /docs）
```

### Dockerfile 单独构建

```bash
docker build -t chem-mix-precheck .
docker run --rm -p 8000:8000 chem-mix-precheck
```

### 本地运行（开发）

```bash
pip install -r requirements.txt -r requirements-dev.txt
uvicorn app.main:app --reload --port 8000
pytest -q          # 运行测试（33 个用例）
```

### 一键跑全部示例

```bash
./examples/run_examples.sh            # 默认打 http://127.0.0.1:8000
```

---

## API

### `POST /v1/precheck`

#### 请求体

```json
{
  "container": {"material": "ptfe", "volume_ml": 2000, "temperature_c": 25},
  "initial":   [{"reagent": "water", "volume_ml": 500, "concentration": 1.0}],
  "allowed_contact": [{"rule_id": "strong_neutralization", "max_seconds": 60}],
  "steps": [
    {"step": 1, "action": "add", "reagent": "hcl", "volume_ml": 50, "concentration": 0.37},
    {"step": 2, "action": "add", "reagent": "sodium_hypochlorite", "volume_ml": 20, "concentration": 0.12}
  ]
}
```

| 字段 | 说明 |
|---|---|
| `container.material` | 容器材质，见 `GET /v1/catalog`（如 `glass`/`ptfe`/`hdpe`/`stainless_steel`…） |
| `container.volume_ml` | 容器容量（mL），模拟中累计体积超限将被拒绝 |
| `container.temperature_c` | 容器温度（°C），影响温控规则与材质耐温判定 |
| `initial[]` | 容器内已有液体：`reagent` + `volume_ml` + `concentration`（质量分数，(0,1]） |
| `allowed_contact[]` | 可选。按 `rule_id` 声明某反应对的允许接触时长（秒）；声明后该对不再即时阻断，改为累计计时，超限触发 `contact_timeout` |
| `steps[]` | 按 `step` 序号升序执行的步骤（序号必须唯一） |

#### 步骤动作（`action`）

| 动作 | 必填字段 | 语义 |
|---|---|---|
| `add` | `reagent`, `volume_ml`, `concentration` | 加入试剂，重新计算各组分浓度 |
| `dilute` | `volume_ml`（可选 `solvent`，默认 `water`） | 加入纯溶剂稀释 |
| `pour_out` | `volume_ml` | 按比例倒出均相混合物（各组分等比减少） |
| `wash` | （可选 `cycles`，默认 1） | 清洗容器：状态清空，**此前保存的所有状态标签失效** |
| `transfer` | `from_label`, `volume_ml` | 将之前用 `label` 保存的混合物状态按比例转回容器 |

任何步骤都可带 `duration_s`（该步骤后混合物静置时长，用于接触计时）和
`label`（保存该步骤完成后的状态快照，供后续 `transfer` 引用）。

#### 响应（200）

```json
{
  "status": "blocked",                // ok | warning | blocked
  "first_trigger_step": 2,            // 首次触发步骤（初始混合物为 0，无触发为 null）
  "findings": [
    {
      "step": 2,
      "category": "incompatible_pair",   // incompatible_pair | contact_timeout | material_incompatibility
      "rule_id": "acid_hypochlorite",
      "severity": "critical",            // critical | high（阻断）| warning（提示）
      "blocking": true,
      "components": [
        {"reagent": "hcl", "name": "盐酸", "concentration_mol_l": 1.050389},
        {"reagent": "sodium_hypochlorite", "name": "次氯酸钠溶液(漂白剂)", "concentration_mol_l": 0.068439}
      ],
      "message": "酸与次氯酸盐混合会释放剧毒氯气(Cl2)"
    }
  ],
  "trace": [                            // 逐步状态（含初始 step 0），按步骤稳定排序
    {"step": 0, "action": "initial", "total_volume_ml": 500.0,
     "components": {"water": 55.509298}, "state_id": "780253ac23db"}
  ],
  "final_state": { "...": "trace 最后一项" }
}
```

- `findings` 按 `(step, rule_id)` 稳定排序；`first_trigger_step` 为最早触发步骤。
- 每条规则在每个清洗周期内只报告一次（首次触发时）；`wash` 后重新混配会再次评估。
- `state_id` 为该步骤后容器状态的指纹，可用于客户端比对计划与实际状态。

#### 逐项拒绝（422）

```json
{
  "detail": [
    {"step": 1, "field": "steps[1].step", "code": "DUPLICATE_STEP", "message": "步骤序号 1 重复"},
    {"step": 1, "field": "steps[1].reagent", "code": "UNKNOWN_REAGENT", "message": "未知试剂 'unobtainium'"},
    {"step": 1, "field": "steps[1].volume_ml", "code": "NON_POSITIVE_VOLUME", "message": "体积必须为正数"},
    {"step": 3, "field": "from_label", "code": "STALE_STATE_REFERENCE", "message": "状态标签 'acid_mix' 保存于清洗之前，清洗后不得再引用旧状态"}
  ]
}
```

校验分两个阶段：先做请求级静态校验（全部问题一次性列出），静态通过后才做
模拟期校验（依赖模拟过程的检查，如倒出过量、引用失效标签）。出错步骤会被跳过并继续模拟，以便一次报告尽量多的问题。

| 错误码 | 含义 |
|---|---|
| `DUPLICATE_STEP` / `INVALID_STEP_NUMBER` | 步骤序号重复 / 非法 |
| `NON_POSITIVE_VOLUME` | 体积非正 |
| `CONCENTRATION_OUT_OF_RANGE` | 浓度越出 (0,1] 或超过该试剂市售浓度上限 |
| `UNKNOWN_REAGENT` / `UNKNOWN_MATERIAL` / `UNKNOWN_RULE_ID` / `UNKNOWN_LABEL` | 未知试剂 / 材质 / 规则 / 状态标签 |
| `STALE_STATE_REFERENCE` | 清洗后仍引用清洗前保存的状态标签 |
| `INSUFFICIENT_VOLUME` | 倒出/转移体积超过现有体积 |
| `CONTAINER_OVERFLOW` | 累计体积超过容器容量 |
| `MISSING_FIELD` / `NON_POSITIVE_DURATION` / `INVALID_CYCLES` / `DUPLICATE_RULE_ID` | 缺字段 / 时长非正 / 清洗次数非法 / 重复声明 |
| `SCHEMA_VALIDATION` | JSON 结构或类型不合法 |

### 其他端点

- `GET /health` — 健康检查
- `GET /v1/catalog` — 试剂目录（含摩尔质量、密度、浓度上限、分类）、材质表、全部规则
- `GET /docs` — Swagger UI

---

## 调用示例

```bash
# 1) 盐酸后加入漂白剂 -> 第 2 步触发 acid_hypochlorite，blocked
curl -sX POST http://127.0.0.1:8000/v1/precheck \
  -H 'Content-Type: application/json' \
  --data-binary @examples/01_blocked_acid_bleach.json | python3 -m json.tool

# 2) 加液-稀释-倒出-回倒-清洗-重新加液 -> ok（演示状态演变与 label/transfer）
curl -sX POST ... @examples/02_ok_dilute_pour_wash.json

# 3) 声明强酸强碱允许接触 60s，步骤累计 75s -> 第 3 步 contact_timeout
curl -sX POST ... @examples/03_contact_timeout.json

# 4) 玻璃容器加氢氟酸 -> material_incompatibility（hf_etches_glass）
curl -sX POST ... @examples/04_material_incompatible.json

# 5) 序号重复 + 未知试剂 + 负体积 + 浓度越界 -> 422 逐项列出
curl -sX POST ... @examples/05_invalid_steps.json

# 6) 清洗后 transfer 引用清洗前的 label -> 422 STALE_STATE_REFERENCE
curl -sX POST ... @examples/06_stale_state_after_wash.json
```

## 内置反应规则（公开常识）

| rule_id | 级别 | 说明 |
|---|---|---|
| `acid_hypochlorite` | critical | 酸 + 次氯酸盐 → 氯气 |
| `ammonia_hypochlorite` | critical | 氨 + 次氯酸盐 → 氯胺 |
| `acid_cyanide` | critical | 酸 + 氰化物 → HCN |
| `acid_sulfide` | critical | 酸 + 硫化物 → H₂S |
| `peroxide_acetone` | critical | 过氧化氢 + 丙酮 → 丙酮过氧化物 |
| `oxidizer_organic` | high | 强氧化剂 + 有机溶剂 → 着火/爆炸 |
| `permanganate_sulfuric` | critical | 高锰酸盐 + 浓硫酸 → Mn₂O₇ |
| `strong_neutralization` | warning | 强酸 + 强碱剧烈放热（可声明允许接触时长） |
| `neutralization_overtemp` | high | ≥40°C 时强酸强碱中和有热失控风险 |
| `hf_etches_glass` | critical | 氢氟酸腐蚀玻璃/陶瓷 |
| `strong_base_aluminum` | high | 强碱腐蚀铝并放氢气 |
| `organic_solvent_polystyrene` | high | 有机溶剂溶解聚苯乙烯 |
| `hypochlorite_stainless` | warning | 次氯酸盐腐蚀不锈钢 |
| `material_temperature_limit` | high | 容器温度超过材质耐温上限 |

## 假设与限制

- 浓度输入为活性组分的**质量分数**；估算浓度按 `体积×密度×质量分数/摩尔质量` 换算为 mol/L，密度取常见市售浓度近似值。
- 体积按可加和近似；不模拟反应消耗、放热、沉淀或气体逸出对组成的改变。
- 接触时间 = 反应对共存期间各步骤 `duration_s` 之和；`wash` 会清零计时与已报告规则。
- 规则表为公开常识的最小集合，可通过 `app/rules.py` 扩展。

## 项目结构

```
app/
  catalog.py   # 试剂/材质目录
  rules.py     # 禁配/材质规则表
  engine.py    # 状态模拟 + 逐项校验 + 规则评估
  models.py    # 请求模型（pydantic）
  main.py      # FastAPI 入口
tests/test_api.py
examples/*.json, run_examples.sh
Dockerfile / docker-compose.yml
```
