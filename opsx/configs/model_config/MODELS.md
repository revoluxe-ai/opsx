# Справочник моделей

## OPSX repair (исполняемый выбор)

Профили: ``models/composer.py`` (repair), ``models/claude_haiku.py`` (Bugbot).
Маршрутизация — ``resolve_model.py``; маппинг в Cursor SDK —
``workflows/repair/cursor_adapter._cursor_sdk_params``.

| Роль | Объект | name |
|------|--------|------|
| repair hops | ``composer_super_low`` | `composer-2.5` |
| ``AgentRole.BUG_BOT`` | ``claude_haiku_bug_bot`` | `claude-haiku-5-5` |

Обновление каталога API ниже: ``python opsx/configs/model_config/render_models_catalog.py``
(после ``sync-cursor-models.py update --write`` или ``/opsx-meta-sync-models``).

---
## Cursor API catalog

- **synced_at:** `2026-10-31T06:03:12+00:00`
- **source:** `cursor_api.GET.v1.models`
- **model_count:** 47 (+ 2 pseudo)
- **machine-readable:** `.cursor/pipeline/shared/MODEL_REGISTRY.json`

### Индекс (все slug)

`inherit`, `auto`, `claude-fable-5`, `claude-fable-5-1`, `claude-haiku-4-5`, `claude-haiku-5-5`, `claude-opus-4-5`, `claude-opus-4-6`, `claude-opus-4-7`, `claude-opus-4-8`, `claude-opus-5`, `claude-opus-5-5`, `claude-sonnet-4`, `claude-sonnet-4-5`, `claude-sonnet-4-6`, `claude-sonnet-5`, `claude-sonnet-5-5`, `composer-2.5`, `deepseek-v41-flash-1017`, `default`, `gemini-2.5-flash`, `gemini-3-flash`, `gemini-3.1-pro`, `gemini-3.5-flash`, `gemini-3.6-flash`, `gemini-3.7-flash`, `gemini-3.8-flash`, `gemini-4-argon`, `glm-5.2`, `glm-5p3`, `glm-5p3-flash`, `gpt-5-mini`, `gpt-5.1`, `gpt-5.2`, `gpt-5.3-codex`, `gpt-5.4`, `gpt-5.4-mini`, `gpt-5.4-nano`, `gpt-5.5`, `gpt-5.6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra`, `grok-4.5`, `grok-4.6`, `grok-4.7`, `kimi-k2.7-code`, `kimi-k3`, `muse-spark-1.3`, `muse-wonder-1.0`

### Сводная таблица

| slug | display | fast? | param ids | variant count | aliases |
|------|---------|-------|-----------|---------------|---------|
| `inherit` | pseudo | — | — | — | — |
| `auto` | pseudo | — | — | — | — |
| `claude-fable-5` | Claude Fable 5 | no | thinking, context, effort | 20 | fable, fable-5, fable-5 |
| `claude-fable-5-1` | Claude Fable 5.1 | no | thinking, context, effort | 20 | fable-5-1 |
| `claude-haiku-4-5` | Claude Haiku 4.5 | no | thinking | 2 | haiku-latest, haiku, haiku-4.5, haiku-4-5 |
| `claude-haiku-5-5` | Claude Haiku 5.5 | no | thinking, context, reasoning_effort | 20 | — |
| `claude-opus-4-5` | Claude Opus 4.5 | no | thinking | 2 | opus, opus-4.5, opus-4-5 |
| `claude-opus-4-6` | Claude Opus 4.6 | no | thinking, context, effort | 16 | opus, opus-4.6, opus-4-6 |
| `claude-opus-4-7` | Claude Opus 4.7 | no | thinking, context, effort, fast | 40 | opus-4.7, opus-4-7 |
| `claude-opus-4-8` | Claude Opus 4.8 | no | thinking, context, effort, fast | 40 | opus-latest, opus, opus-4.8, opus-4-8 |
| `claude-opus-5` | Claude Opus 5 | no | thinking, context, effort, fast | 32 | opus-latest, opus, opus-5 |
| `claude-opus-5-5` | Claude Opus 5.5 | no | context, effort, fast | 20 | opus-latest, opus, opus-5.5, opus-5-5 |
| `claude-sonnet-4` | Claude Sonnet 4 | no | thinking, context | 2 | sonnet, sonnet-4 |
| `claude-sonnet-4-5` | Claude Sonnet 4.5 | no | thinking, context | 2 | sonnet, sonnet-4.5, sonnet-4-5 |
| `claude-sonnet-4-6` | Claude Sonnet 4.6 | no | thinking, context, effort | 16 | sonnet-latest, sonnet, sonnet-4.6, sonnet-4-6 |
| `claude-sonnet-5` | Claude Sonnet 5 | no | thinking, context, effort | 20 | sonnet-latest, sonnet-5 |
| `claude-sonnet-5-5` | Claude Sonnet 5.5 | no | context, reasoning_effort | 10 | — |
| `composer-2.5` | Composer 2.5 | no | fast | 2 | composer-latest, composer, composer-2-5 |
| `deepseek-v41-flash-1017` | DeepSeek V4.1 Flash 1017 | no | reasoning_effort | 5 | — |
| `default` | Auto | no | — | 1 | auto |
| `gemini-2.5-flash` | Gemini 2.5 Flash | no | — | 1 | gemini-flash |
| `gemini-3-flash` | Gemini 3 Flash | no | — | 1 | — |
| `gemini-3.1-pro` | Gemini 3.1 Pro | no | — | 1 | gemini-latest, gemini-pro-latest, gemini, gemini-pro |
| `gemini-3.5-flash` | Gemini 3.5 Flash | no | — | 1 | gemini-flash-latest, gemini-flash |
| `gemini-3.6-flash` | Gemini 3.6 Flash | no | effort | 4 | gemini-flash-latest, gemini-flash |
| `gemini-3.7-flash` | Gemini 3.7 Flash | no | effort | 3 | gemini-flash-latest, gemini-flash |
| `gemini-3.8-flash` | Gemini 3.8 Flash | no | reasoning_effort | 3 | — |
| `gemini-4-argon` | Gemini 4 Argon | no | reasoning_effort | 3 | — |
| `glm-5.2` | GLM 5.2 | no | reasoning | 2 | — |
| `glm-5p3` | GLM 5.3 | no | reasoning | 3 | — |
| `glm-5p3-flash` | GLM 5.3 Flash | no | reasoning | 3 | — |
| `gpt-5-mini` | GPT-5 Mini | no | — | 1 | gpt-mini |
| `gpt-5.1` | GPT-5.1 | no | reasoning | 3 | gpt |
| `gpt-5.2` | GPT-5.2 | no | reasoning, fast | 8 | gpt |
| `gpt-5.3-codex` | Codex 5.3 | no | reasoning, fast | 8 | codex-latest, codex, codex-5.3 |
| `gpt-5.4` | GPT-5.4 | no | context, reasoning, fast | 15 | gpt |
| `gpt-5.4-mini` | GPT-5.4 Mini | no | reasoning | 5 | gpt-mini-latest, gpt-mini |
| `gpt-5.4-nano` | GPT-5.4 Nano | no | reasoning | 5 | gpt-nano-latest, gpt-nano |
| `gpt-5.5` | GPT-5.5 | no | context, reasoning, fast | 15 | gpt-5-5 |
| `gpt-5.6-luna` | GPT-5.6 Luna | no | context, reasoning, fast | 24 | gpt-5-6-luna |
| `gpt-5.6-sol` | GPT-5.6 Sol | no | context, reasoning, fast | 24 | gpt-latest, gpt, gpt-5-6-sol, gpt-5.6 |
| `gpt-5.6-terra` | GPT-5.6 Terra | no | context, reasoning, fast | 24 | gpt-5-6-terra |
| `grok-4.5` | Grok 4.5 | no | effort, fast | 6 | — |
| `grok-4.6` | Grok 4.6 | no | effort, fast | 8 | — |
| `grok-4.7` | Grok 4.7 | no | context, reasoning_effort, fast | 16 | — |
| `kimi-k2.7-code` | Kimi K2.7 Code | no | — | 1 | — |
| `kimi-k3` | Kimi K3 | no | reasoning | 3 | — |
| `muse-spark-1.3` | Muse Spark 1.3 | no | context, effort | 12 | muse-spark, muse |
| `muse-wonder-1.0` | Muse Wonder | no | context, reasoning_effort | 12 | — |

### Полная выгрузка по моделям

#### `inherit` (pseudo)

Parent chat agent model (Task model: inherit)

#### `auto` (pseudo)

Explicit platform Auto (not silent default)

#### `claude-fable-5`

- **display_name:** Claude Fable 5
- **fast_variant:** no
- **aliases:** `fable`, `fable-5`, `fable-5`

**parameters (API):**
- **`thinking`** (Thinking): `false`, `true`
- **`context`** (Context): `300k (300K)`, `1m (1M)`
- **`effort`** (Effort): `low (Low)`, `medium (Medium)`, `high (High)`, `xhigh (Extra High)`, `max (Max)`

**variants (preset param combinations):**
- Claude Fable 5: `thinking=false`, `context=300k`, `effort=low`
- Claude Fable 5: `thinking=false`, `context=300k`, `effort=medium`
- Claude Fable 5: `thinking=false`, `context=300k`, `effort=high`
- Claude Fable 5: `thinking=false`, `context=300k`, `effort=xhigh`
- Claude Fable 5: `thinking=false`, `context=300k`, `effort=max`
- Claude Fable 5: `thinking=false`, `context=1m`, `effort=low`
- Claude Fable 5: `thinking=false`, `context=1m`, `effort=medium`
- Claude Fable 5: `thinking=false`, `context=1m`, `effort=high`
- Claude Fable 5: `thinking=false`, `context=1m`, `effort=xhigh`
- Claude Fable 5: `thinking=false`, `context=1m`, `effort=max`
- Claude Fable 5: `thinking=true`, `context=300k`, `effort=low`
- Claude Fable 5: `thinking=true`, `context=300k`, `effort=medium`
- Claude Fable 5: `thinking=true`, `context=300k`, `effort=high`
- Claude Fable 5: `thinking=true`, `context=300k`, `effort=xhigh`
- Claude Fable 5: `thinking=true`, `context=300k`, `effort=max`
- Claude Fable 5: `thinking=true`, `context=1m`, `effort=low`
- Claude Fable 5: `thinking=true`, `context=1m`, `effort=medium`
- Claude Fable 5 **(default)**: `thinking=true`, `context=1m`, `effort=high`
- Claude Fable 5: `thinking=true`, `context=1m`, `effort=xhigh`
- Claude Fable 5: `thinking=true`, `context=1m`, `effort=max`

#### `claude-fable-5-1`

- **display_name:** Claude Fable 5.1
- **fast_variant:** no
- **aliases:** `fable-5-1`

**parameters (API):**
- **`thinking`** (Thinking): `false`, `true`
- **`context`** (Context): `300k (300K)`, `1m (1M)`
- **`effort`** (Effort): `low (Low)`, `medium (Medium)`, `high (High)`, `xhigh (Extra High)`, `max (Max)`

**variants (preset param combinations):**
- Claude Fable 5.1: `thinking=false`, `context=300k`, `effort=low`
- Claude Fable 5.1: `thinking=false`, `context=300k`, `effort=medium`
- Claude Fable 5.1: `thinking=false`, `context=300k`, `effort=high`
- Claude Fable 5.1: `thinking=false`, `context=300k`, `effort=xhigh`
- Claude Fable 5.1: `thinking=false`, `context=300k`, `effort=max`
- Claude Fable 5.1: `thinking=false`, `context=1m`, `effort=low`
- Claude Fable 5.1: `thinking=false`, `context=1m`, `effort=medium`
- Claude Fable 5.1: `thinking=false`, `context=1m`, `effort=high`
- Claude Fable 5.1: `thinking=false`, `context=1m`, `effort=xhigh`
- Claude Fable 5.1: `thinking=false`, `context=1m`, `effort=max`
- Claude Fable 5.1: `thinking=true`, `context=300k`, `effort=low`
- Claude Fable 5.1: `thinking=true`, `context=300k`, `effort=medium`
- Claude Fable 5.1: `thinking=true`, `context=300k`, `effort=high`
- Claude Fable 5.1: `thinking=true`, `context=300k`, `effort=xhigh`
- Claude Fable 5.1: `thinking=true`, `context=300k`, `effort=max`
- Claude Fable 5.1: `thinking=true`, `context=1m`, `effort=low`
- Claude Fable 5.1: `thinking=true`, `context=1m`, `effort=medium`
- Claude Fable 5.1 **(default)**: `thinking=true`, `context=1m`, `effort=high`
- Claude Fable 5.1: `thinking=true`, `context=1m`, `effort=xhigh`
- Claude Fable 5.1: `thinking=true`, `context=1m`, `effort=max`

#### `claude-haiku-4-5`

- **display_name:** Claude Haiku 4.5
- **fast_variant:** no
- **aliases:** `haiku-latest`, `haiku`, `haiku-4.5`, `haiku-4-5`

**parameters (API):**
- **`thinking`** (Thinking): `false`, `true`

**variants (preset param combinations):**
- Claude Haiku 4.5: `thinking=false`
- Claude Haiku 4.5 **(default)**: `thinking=true`

#### `claude-haiku-5-5`

- **display_name:** Claude Haiku 5.5
- **fast_variant:** no

**parameters (API):**
- **`thinking`** (Thinking): `false`, `true`
- **`context`** (Context): `300k (300K)`, `1m (1M)`
- **`reasoning_effort`** (Effort): `low (Low)`, `medium (Medium)`, `high (High)`, `xhigh (Extra High)`, `max (Max)`

**variants (preset param combinations):**
- Claude Haiku 5.5  Low No Thinking: `thinking=false`, `context=300k`, `reasoning_effort=low`
- Claude Haiku 5.5  Medium No Thinking: `thinking=false`, `context=300k`, `reasoning_effort=medium`
- Claude Haiku 5.5  High No Thinking: `thinking=false`, `context=300k`, `reasoning_effort=high`
- Claude Haiku 5.5  Extra High No Thinking: `thinking=false`, `context=300k`, `reasoning_effort=xhigh`
- Claude Haiku 5.5  Max No Thinking: `thinking=false`, `context=300k`, `reasoning_effort=max`
- Claude Haiku 5.5 1M Low No Thinking: `thinking=false`, `context=1m`, `reasoning_effort=low`
- Claude Haiku 5.5 1M Medium No Thinking: `thinking=false`, `context=1m`, `reasoning_effort=medium`
- Claude Haiku 5.5 1M High No Thinking: `thinking=false`, `context=1m`, `reasoning_effort=high`
- Claude Haiku 5.5 1M Extra High No Thinking: `thinking=false`, `context=1m`, `reasoning_effort=xhigh`
- Claude Haiku 5.5 1M Max No Thinking: `thinking=false`, `context=1m`, `reasoning_effort=max`
- Claude Haiku 5.5  Low: `thinking=true`, `context=300k`, `reasoning_effort=low`
- Claude Haiku 5.5  Medium: `thinking=true`, `context=300k`, `reasoning_effort=medium`
- Claude Haiku 5.5  High: `thinking=true`, `context=300k`, `reasoning_effort=high`
- Claude Haiku 5.5  Extra High: `thinking=true`, `context=300k`, `reasoning_effort=xhigh`
- Claude Haiku 5.5  Max: `thinking=true`, `context=300k`, `reasoning_effort=max`
- Claude Haiku 5.5 1M Low: `thinking=true`, `context=1m`, `reasoning_effort=low`
- Claude Haiku 5.5 1M Medium: `thinking=true`, `context=1m`, `reasoning_effort=medium`
- Claude Haiku 5.5 1M High **(default)**: `thinking=true`, `context=1m`, `reasoning_effort=high`
- Claude Haiku 5.5 1M Extra High: `thinking=true`, `context=1m`, `reasoning_effort=xhigh`
- Claude Haiku 5.5 1M Max: `thinking=true`, `context=1m`, `reasoning_effort=max`

#### `claude-opus-4-5`

- **display_name:** Claude Opus 4.5
- **fast_variant:** no
- **aliases:** `opus`, `opus-4.5`, `opus-4-5`

**parameters (API):**
- **`thinking`** (Thinking): `false`, `true`

**variants (preset param combinations):**
- Claude Opus 4.5: `thinking=false`
- Claude Opus 4.5 **(default)**: `thinking=true`

#### `claude-opus-4-6`

- **display_name:** Claude Opus 4.6
- **fast_variant:** no
- **aliases:** `opus`, `opus-4.6`, `opus-4-6`

**parameters (API):**
- **`thinking`** (Thinking): `false`, `true`
- **`context`** (Context): `200k (200K)`, `1m (1M)`
- **`effort`** (Effort): `low (Low)`, `medium (Medium)`, `high (High)`, `max (Max)`

**variants (preset param combinations):**
- Claude Opus 4.6: `thinking=false`, `context=200k`, `effort=low`
- Claude Opus 4.6: `thinking=false`, `context=200k`, `effort=medium`
- Claude Opus 4.6: `thinking=false`, `context=200k`, `effort=high`
- Claude Opus 4.6: `thinking=false`, `context=200k`, `effort=max`
- Claude Opus 4.6: `thinking=false`, `context=1m`, `effort=low`
- Claude Opus 4.6: `thinking=false`, `context=1m`, `effort=medium`
- Claude Opus 4.6: `thinking=false`, `context=1m`, `effort=high`
- Claude Opus 4.6: `thinking=false`, `context=1m`, `effort=max`
- Claude Opus 4.6: `thinking=true`, `context=200k`, `effort=low`
- Claude Opus 4.6: `thinking=true`, `context=200k`, `effort=medium`
- Claude Opus 4.6: `thinking=true`, `context=200k`, `effort=high`
- Claude Opus 4.6: `thinking=true`, `context=200k`, `effort=max`
- Claude Opus 4.6: `thinking=true`, `context=1m`, `effort=low`
- Claude Opus 4.6: `thinking=true`, `context=1m`, `effort=medium`
- Claude Opus 4.6 **(default)**: `thinking=true`, `context=1m`, `effort=high`
- Claude Opus 4.6: `thinking=true`, `context=1m`, `effort=max`

#### `claude-opus-4-7`

- **display_name:** Claude Opus 4.7
- **fast_variant:** no
- **aliases:** `opus-4.7`, `opus-4-7`

**parameters (API):**
- **`thinking`** (Thinking): `false`, `true`
- **`context`** (Context): `300k (300K)`, `1m (1M)`
- **`effort`** (Effort): `low (Low)`, `medium (Medium)`, `high (High)`, `xhigh (Extra High)`, `max (Max)`
- **`fast`** (Fast): `false`, `true (Fast)`

**variants (preset param combinations):**
- Claude Opus 4.7: `cyber=false`, `thinking=false`, `context=300k`, `effort=low`, `fast=false`
- Claude Opus 4.7: `cyber=false`, `thinking=false`, `context=300k`, `effort=low`, `fast=true`
- Claude Opus 4.7: `cyber=false`, `thinking=false`, `context=300k`, `effort=medium`, `fast=false`
- Claude Opus 4.7: `cyber=false`, `thinking=false`, `context=300k`, `effort=medium`, `fast=true`
- Claude Opus 4.7: `cyber=false`, `thinking=false`, `context=300k`, `effort=high`, `fast=false`
- Claude Opus 4.7: `cyber=false`, `thinking=false`, `context=300k`, `effort=high`, `fast=true`
- Claude Opus 4.7: `cyber=false`, `thinking=false`, `context=300k`, `effort=xhigh`, `fast=false`
- Claude Opus 4.7: `cyber=false`, `thinking=false`, `context=300k`, `effort=xhigh`, `fast=true`
- Claude Opus 4.7: `cyber=false`, `thinking=false`, `context=300k`, `effort=max`, `fast=false`
- Claude Opus 4.7: `cyber=false`, `thinking=false`, `context=300k`, `effort=max`, `fast=true`
- Claude Opus 4.7: `cyber=false`, `thinking=false`, `context=1m`, `effort=low`, `fast=false`
- Claude Opus 4.7: `cyber=false`, `thinking=false`, `context=1m`, `effort=low`, `fast=true`
- Claude Opus 4.7: `cyber=false`, `thinking=false`, `context=1m`, `effort=medium`, `fast=false`
- Claude Opus 4.7: `cyber=false`, `thinking=false`, `context=1m`, `effort=medium`, `fast=true`
- Claude Opus 4.7: `cyber=false`, `thinking=false`, `context=1m`, `effort=high`, `fast=false`
- Claude Opus 4.7: `cyber=false`, `thinking=false`, `context=1m`, `effort=high`, `fast=true`
- Claude Opus 4.7: `cyber=false`, `thinking=false`, `context=1m`, `effort=xhigh`, `fast=false`
- Claude Opus 4.7: `cyber=false`, `thinking=false`, `context=1m`, `effort=xhigh`, `fast=true`
- Claude Opus 4.7: `cyber=false`, `thinking=false`, `context=1m`, `effort=max`, `fast=false`
- Claude Opus 4.7: `cyber=false`, `thinking=false`, `context=1m`, `effort=max`, `fast=true`
- Claude Opus 4.7: `cyber=false`, `thinking=true`, `context=300k`, `effort=low`, `fast=false`
- Claude Opus 4.7: `cyber=false`, `thinking=true`, `context=300k`, `effort=low`, `fast=true`
- Claude Opus 4.7: `cyber=false`, `thinking=true`, `context=300k`, `effort=medium`, `fast=false`
- Claude Opus 4.7: `cyber=false`, `thinking=true`, `context=300k`, `effort=medium`, `fast=true`
- Claude Opus 4.7: `cyber=false`, `thinking=true`, `context=300k`, `effort=high`, `fast=false`
- Claude Opus 4.7: `cyber=false`, `thinking=true`, `context=300k`, `effort=high`, `fast=true`
- Claude Opus 4.7: `cyber=false`, `thinking=true`, `context=300k`, `effort=xhigh`, `fast=false`
- Claude Opus 4.7: `cyber=false`, `thinking=true`, `context=300k`, `effort=xhigh`, `fast=true`
- Claude Opus 4.7: `cyber=false`, `thinking=true`, `context=300k`, `effort=max`, `fast=false`
- Claude Opus 4.7: `cyber=false`, `thinking=true`, `context=300k`, `effort=max`, `fast=true`
- Claude Opus 4.7: `cyber=false`, `thinking=true`, `context=1m`, `effort=low`, `fast=false`
- Claude Opus 4.7: `cyber=false`, `thinking=true`, `context=1m`, `effort=low`, `fast=true`
- Claude Opus 4.7: `cyber=false`, `thinking=true`, `context=1m`, `effort=medium`, `fast=false`
- Claude Opus 4.7: `cyber=false`, `thinking=true`, `context=1m`, `effort=medium`, `fast=true`
- Claude Opus 4.7: `cyber=false`, `thinking=true`, `context=1m`, `effort=high`, `fast=false`
- Claude Opus 4.7: `cyber=false`, `thinking=true`, `context=1m`, `effort=high`, `fast=true`
- Claude Opus 4.7 **(default)**: `cyber=false`, `thinking=true`, `context=1m`, `effort=xhigh`, `fast=false`
- Claude Opus 4.7: `cyber=false`, `thinking=true`, `context=1m`, `effort=xhigh`, `fast=true`
- Claude Opus 4.7: `cyber=false`, `thinking=true`, `context=1m`, `effort=max`, `fast=false`
- Claude Opus 4.7: `cyber=false`, `thinking=true`, `context=1m`, `effort=max`, `fast=true`

#### `claude-opus-4-8`

- **display_name:** Claude Opus 4.8
- **fast_variant:** no
- **aliases:** `opus-latest`, `opus`, `opus-4.8`, `opus-4-8`

**parameters (API):**
- **`thinking`** (Thinking): `false`, `true`
- **`context`** (Context): `300k (300K)`, `1m (1M)`
- **`effort`** (Effort): `low (Low)`, `medium (Medium)`, `high (High)`, `xhigh (Extra High)`, `max (Max)`
- **`fast`** (Fast): `false`, `true (Fast)`

**variants (preset param combinations):**
- Claude Opus 4.8: `cyber=false`, `thinking=false`, `context=300k`, `effort=low`, `fast=false`
- Claude Opus 4.8: `cyber=false`, `thinking=false`, `context=300k`, `effort=low`, `fast=true`
- Claude Opus 4.8: `cyber=false`, `thinking=false`, `context=300k`, `effort=medium`, `fast=false`
- Claude Opus 4.8: `cyber=false`, `thinking=false`, `context=300k`, `effort=medium`, `fast=true`
- Claude Opus 4.8: `cyber=false`, `thinking=false`, `context=300k`, `effort=high`, `fast=false`
- Claude Opus 4.8: `cyber=false`, `thinking=false`, `context=300k`, `effort=high`, `fast=true`
- Claude Opus 4.8: `cyber=false`, `thinking=false`, `context=300k`, `effort=xhigh`, `fast=false`
- Claude Opus 4.8: `cyber=false`, `thinking=false`, `context=300k`, `effort=xhigh`, `fast=true`
- Claude Opus 4.8: `cyber=false`, `thinking=false`, `context=300k`, `effort=max`, `fast=false`
- Claude Opus 4.8: `cyber=false`, `thinking=false`, `context=300k`, `effort=max`, `fast=true`
- Claude Opus 4.8: `cyber=false`, `thinking=false`, `context=1m`, `effort=low`, `fast=false`
- Claude Opus 4.8: `cyber=false`, `thinking=false`, `context=1m`, `effort=low`, `fast=true`
- Claude Opus 4.8: `cyber=false`, `thinking=false`, `context=1m`, `effort=medium`, `fast=false`
- Claude Opus 4.8: `cyber=false`, `thinking=false`, `context=1m`, `effort=medium`, `fast=true`
- Claude Opus 4.8: `cyber=false`, `thinking=false`, `context=1m`, `effort=high`, `fast=false`
- Claude Opus 4.8: `cyber=false`, `thinking=false`, `context=1m`, `effort=high`, `fast=true`
- Claude Opus 4.8: `cyber=false`, `thinking=false`, `context=1m`, `effort=xhigh`, `fast=false`
- Claude Opus 4.8: `cyber=false`, `thinking=false`, `context=1m`, `effort=xhigh`, `fast=true`
- Claude Opus 4.8: `cyber=false`, `thinking=false`, `context=1m`, `effort=max`, `fast=false`
- Claude Opus 4.8: `cyber=false`, `thinking=false`, `context=1m`, `effort=max`, `fast=true`
- Claude Opus 4.8: `cyber=false`, `thinking=true`, `context=300k`, `effort=low`, `fast=false`
- Claude Opus 4.8: `cyber=false`, `thinking=true`, `context=300k`, `effort=low`, `fast=true`
- Claude Opus 4.8: `cyber=false`, `thinking=true`, `context=300k`, `effort=medium`, `fast=false`
- Claude Opus 4.8: `cyber=false`, `thinking=true`, `context=300k`, `effort=medium`, `fast=true`
- Claude Opus 4.8: `cyber=false`, `thinking=true`, `context=300k`, `effort=high`, `fast=false`
- Claude Opus 4.8: `cyber=false`, `thinking=true`, `context=300k`, `effort=high`, `fast=true`
- Claude Opus 4.8: `cyber=false`, `thinking=true`, `context=300k`, `effort=xhigh`, `fast=false`
- Claude Opus 4.8: `cyber=false`, `thinking=true`, `context=300k`, `effort=xhigh`, `fast=true`
- Claude Opus 4.8: `cyber=false`, `thinking=true`, `context=300k`, `effort=max`, `fast=false`
- Claude Opus 4.8: `cyber=false`, `thinking=true`, `context=300k`, `effort=max`, `fast=true`
- Claude Opus 4.8: `cyber=false`, `thinking=true`, `context=1m`, `effort=low`, `fast=false`
- Claude Opus 4.8: `cyber=false`, `thinking=true`, `context=1m`, `effort=low`, `fast=true`
- Claude Opus 4.8: `cyber=false`, `thinking=true`, `context=1m`, `effort=medium`, `fast=false`
- Claude Opus 4.8: `cyber=false`, `thinking=true`, `context=1m`, `effort=medium`, `fast=true`
- Claude Opus 4.8 **(default)**: `cyber=false`, `thinking=true`, `context=1m`, `effort=high`, `fast=false`
- Claude Opus 4.8: `cyber=false`, `thinking=true`, `context=1m`, `effort=high`, `fast=true`
- Claude Opus 4.8: `cyber=false`, `thinking=true`, `context=1m`, `effort=xhigh`, `fast=false`
- Claude Opus 4.8: `cyber=false`, `thinking=true`, `context=1m`, `effort=xhigh`, `fast=true`
- Claude Opus 4.8: `cyber=false`, `thinking=true`, `context=1m`, `effort=max`, `fast=false`
- Claude Opus 4.8: `cyber=false`, `thinking=true`, `context=1m`, `effort=max`, `fast=true`

#### `claude-opus-5`

- **display_name:** Claude Opus 5
- **fast_variant:** no
- **aliases:** `opus-latest`, `opus`, `opus-5`

**parameters (API):**
- **`thinking`** (Thinking): `false`, `true`
- **`context`** (Context): `300k (300K)`, `1m (1M)`
- **`effort`** (Effort): `low (Low)`, `medium (Medium)`, `high (High)`, `xhigh (Extra High)`, `max (Max)`
- **`fast`** (Fast): `false`, `true (Fast)`

**variants (preset param combinations):**
- Claude Opus 5: `cyber=false`, `thinking=false`, `context=300k`, `effort=low`, `fast=false`
- Claude Opus 5: `cyber=false`, `thinking=false`, `context=300k`, `effort=low`, `fast=true`
- Claude Opus 5: `cyber=false`, `thinking=false`, `context=300k`, `effort=medium`, `fast=false`
- Claude Opus 5: `cyber=false`, `thinking=false`, `context=300k`, `effort=medium`, `fast=true`
- Claude Opus 5: `cyber=false`, `thinking=false`, `context=300k`, `effort=high`, `fast=false`
- Claude Opus 5: `cyber=false`, `thinking=false`, `context=300k`, `effort=high`, `fast=true`
- Claude Opus 5: `cyber=false`, `thinking=false`, `context=1m`, `effort=low`, `fast=false`
- Claude Opus 5: `cyber=false`, `thinking=false`, `context=1m`, `effort=low`, `fast=true`
- Claude Opus 5: `cyber=false`, `thinking=false`, `context=1m`, `effort=medium`, `fast=false`
- Claude Opus 5: `cyber=false`, `thinking=false`, `context=1m`, `effort=medium`, `fast=true`
- Claude Opus 5: `cyber=false`, `thinking=false`, `context=1m`, `effort=high`, `fast=false`
- Claude Opus 5: `cyber=false`, `thinking=false`, `context=1m`, `effort=high`, `fast=true`
- Claude Opus 5: `cyber=false`, `thinking=true`, `context=300k`, `effort=low`, `fast=false`
- Claude Opus 5: `cyber=false`, `thinking=true`, `context=300k`, `effort=low`, `fast=true`
- Claude Opus 5: `cyber=false`, `thinking=true`, `context=300k`, `effort=medium`, `fast=false`
- Claude Opus 5: `cyber=false`, `thinking=true`, `context=300k`, `effort=medium`, `fast=true`
- Claude Opus 5: `cyber=false`, `thinking=true`, `context=300k`, `effort=high`, `fast=false`
- Claude Opus 5: `cyber=false`, `thinking=true`, `context=300k`, `effort=high`, `fast=true`
- Claude Opus 5: `cyber=false`, `thinking=true`, `context=300k`, `effort=xhigh`, `fast=false`
- Claude Opus 5: `cyber=false`, `thinking=true`, `context=300k`, `effort=xhigh`, `fast=true`
- Claude Opus 5: `cyber=false`, `thinking=true`, `context=300k`, `effort=max`, `fast=false`
- Claude Opus 5: `cyber=false`, `thinking=true`, `context=300k`, `effort=max`, `fast=true`
- Claude Opus 5: `cyber=false`, `thinking=true`, `context=1m`, `effort=low`, `fast=false`
- Claude Opus 5: `cyber=false`, `thinking=true`, `context=1m`, `effort=low`, `fast=true`
- Claude Opus 5: `cyber=false`, `thinking=true`, `context=1m`, `effort=medium`, `fast=false`
- Claude Opus 5: `cyber=false`, `thinking=true`, `context=1m`, `effort=medium`, `fast=true`
- Claude Opus 5 **(default)**: `cyber=false`, `thinking=true`, `context=1m`, `effort=high`, `fast=false`
- Claude Opus 5: `cyber=false`, `thinking=true`, `context=1m`, `effort=high`, `fast=true`
- Claude Opus 5: `cyber=false`, `thinking=true`, `context=1m`, `effort=xhigh`, `fast=false`
- Claude Opus 5: `cyber=false`, `thinking=true`, `context=1m`, `effort=xhigh`, `fast=true`
- Claude Opus 5: `cyber=false`, `thinking=true`, `context=1m`, `effort=max`, `fast=false`
- Claude Opus 5: `cyber=false`, `thinking=true`, `context=1m`, `effort=max`, `fast=true`

#### `claude-opus-5-5`

- **display_name:** Claude Opus 5.5
- **fast_variant:** no
- **aliases:** `opus-latest`, `opus`, `opus-5.5`, `opus-5-5`

**parameters (API):**
- **`context`** (Context): `300k (300K)`, `1m (1M)`
- **`effort`** (Effort): `low (Low)`, `medium (Medium)`, `high (High)`, `xhigh (Extra High)`, `max (Max)`
- **`fast`** (Fast): `false`, `true (Fast)`

**variants (preset param combinations):**
- Claude Opus 5.5: `cyber=false`, `context=300k`, `effort=low`, `fast=false`
- Claude Opus 5.5: `cyber=false`, `context=300k`, `effort=low`, `fast=true`
- Claude Opus 5.5: `cyber=false`, `context=300k`, `effort=medium`, `fast=false`
- Claude Opus 5.5: `cyber=false`, `context=300k`, `effort=medium`, `fast=true`
- Claude Opus 5.5: `cyber=false`, `context=300k`, `effort=high`, `fast=false`
- Claude Opus 5.5: `cyber=false`, `context=300k`, `effort=high`, `fast=true`
- Claude Opus 5.5: `cyber=false`, `context=300k`, `effort=xhigh`, `fast=false`
- Claude Opus 5.5: `cyber=false`, `context=300k`, `effort=xhigh`, `fast=true`
- Claude Opus 5.5: `cyber=false`, `context=300k`, `effort=max`, `fast=false`
- Claude Opus 5.5: `cyber=false`, `context=300k`, `effort=max`, `fast=true`
- Claude Opus 5.5: `cyber=false`, `context=1m`, `effort=low`, `fast=false`
- Claude Opus 5.5: `cyber=false`, `context=1m`, `effort=low`, `fast=true`
- Claude Opus 5.5 **(default)**: `cyber=false`, `context=1m`, `effort=medium`, `fast=false`
- Claude Opus 5.5: `cyber=false`, `context=1m`, `effort=medium`, `fast=true`
- Claude Opus 5.5: `cyber=false`, `context=1m`, `effort=high`, `fast=false`
- Claude Opus 5.5: `cyber=false`, `context=1m`, `effort=high`, `fast=true`
- Claude Opus 5.5: `cyber=false`, `context=1m`, `effort=xhigh`, `fast=false`
- Claude Opus 5.5: `cyber=false`, `context=1m`, `effort=xhigh`, `fast=true`
- Claude Opus 5.5: `cyber=false`, `context=1m`, `effort=max`, `fast=false`
- Claude Opus 5.5: `cyber=false`, `context=1m`, `effort=max`, `fast=true`

#### `claude-sonnet-4`

- **display_name:** Claude Sonnet 4
- **fast_variant:** no
- **aliases:** `sonnet`, `sonnet-4`

**parameters (API):**
- **`thinking`** (Thinking): `false`, `true`
- **`context`** (Context): `200k (200K)`

**variants (preset param combinations):**
- Claude Sonnet 4 **(default)**: `thinking=false`, `context=200k`
- Claude Sonnet 4: `thinking=true`, `context=200k`

#### `claude-sonnet-4-5`

- **display_name:** Claude Sonnet 4.5
- **fast_variant:** no
- **aliases:** `sonnet`, `sonnet-4.5`, `sonnet-4-5`

**parameters (API):**
- **`thinking`** (Thinking): `false`, `true`
- **`context`** (Context): `200k (200K)`

**variants (preset param combinations):**
- Claude Sonnet 4.5: `thinking=false`, `context=200k`
- Claude Sonnet 4.5 **(default)**: `thinking=true`, `context=200k`

#### `claude-sonnet-4-6`

- **display_name:** Claude Sonnet 4.6
- **fast_variant:** no
- **aliases:** `sonnet-latest`, `sonnet`, `sonnet-4.6`, `sonnet-4-6`

**parameters (API):**
- **`thinking`** (Thinking): `false`, `true`
- **`context`** (Context): `200k (200K)`, `1m (1M)`
- **`effort`** (Effort): `low (Low)`, `medium (Medium)`, `high (High)`, `max (Max)`

**variants (preset param combinations):**
- Claude Sonnet 4.6: `thinking=false`, `context=200k`, `effort=low`
- Claude Sonnet 4.6: `thinking=false`, `context=200k`, `effort=medium`
- Claude Sonnet 4.6: `thinking=false`, `context=200k`, `effort=high`
- Claude Sonnet 4.6: `thinking=false`, `context=200k`, `effort=max`
- Claude Sonnet 4.6: `thinking=false`, `context=1m`, `effort=low`
- Claude Sonnet 4.6: `thinking=false`, `context=1m`, `effort=medium`
- Claude Sonnet 4.6: `thinking=false`, `context=1m`, `effort=high`
- Claude Sonnet 4.6: `thinking=false`, `context=1m`, `effort=max`
- Claude Sonnet 4.6: `thinking=true`, `context=200k`, `effort=low`
- Claude Sonnet 4.6: `thinking=true`, `context=200k`, `effort=medium`
- Claude Sonnet 4.6: `thinking=true`, `context=200k`, `effort=high`
- Claude Sonnet 4.6: `thinking=true`, `context=200k`, `effort=max`
- Claude Sonnet 4.6: `thinking=true`, `context=1m`, `effort=low`
- Claude Sonnet 4.6 **(default)**: `thinking=true`, `context=1m`, `effort=medium`
- Claude Sonnet 4.6: `thinking=true`, `context=1m`, `effort=high`
- Claude Sonnet 4.6: `thinking=true`, `context=1m`, `effort=max`

#### `claude-sonnet-5`

- **display_name:** Claude Sonnet 5
- **fast_variant:** no
- **aliases:** `sonnet-latest`, `sonnet-5`

**parameters (API):**
- **`thinking`** (Thinking): `false`, `true`
- **`context`** (Context): `300k (300K)`, `1m (1M)`
- **`effort`** (Effort): `low (Low)`, `medium (Medium)`, `high (High)`, `xhigh (Extra High)`, `max (Max)`

**variants (preset param combinations):**
- Claude Sonnet 5: `thinking=false`, `context=300k`, `effort=low`
- Claude Sonnet 5: `thinking=false`, `context=300k`, `effort=medium`
- Claude Sonnet 5: `thinking=false`, `context=300k`, `effort=high`
- Claude Sonnet 5: `thinking=false`, `context=300k`, `effort=xhigh`
- Claude Sonnet 5: `thinking=false`, `context=300k`, `effort=max`
- Claude Sonnet 5: `thinking=false`, `context=1m`, `effort=low`
- Claude Sonnet 5: `thinking=false`, `context=1m`, `effort=medium`
- Claude Sonnet 5: `thinking=false`, `context=1m`, `effort=high`
- Claude Sonnet 5: `thinking=false`, `context=1m`, `effort=xhigh`
- Claude Sonnet 5: `thinking=false`, `context=1m`, `effort=max`
- Claude Sonnet 5: `thinking=true`, `context=300k`, `effort=low`
- Claude Sonnet 5: `thinking=true`, `context=300k`, `effort=medium`
- Claude Sonnet 5: `thinking=true`, `context=300k`, `effort=high`
- Claude Sonnet 5: `thinking=true`, `context=300k`, `effort=xhigh`
- Claude Sonnet 5: `thinking=true`, `context=300k`, `effort=max`
- Claude Sonnet 5: `thinking=true`, `context=1m`, `effort=low`
- Claude Sonnet 5: `thinking=true`, `context=1m`, `effort=medium`
- Claude Sonnet 5 **(default)**: `thinking=true`, `context=1m`, `effort=high`
- Claude Sonnet 5: `thinking=true`, `context=1m`, `effort=xhigh`
- Claude Sonnet 5: `thinking=true`, `context=1m`, `effort=max`

#### `claude-sonnet-5-5`

- **display_name:** Claude Sonnet 5.5
- **fast_variant:** no

**parameters (API):**
- **`context`** (Context): `300k (300K)`, `1m (1M)`
- **`reasoning_effort`** (Effort): `low (Low)`, `medium (Medium)`, `high (High)`, `xhigh (Extra High)`, `max (Max)`

**variants (preset param combinations):**
- Claude Sonnet 5.5  Low: `context=300k`, `reasoning_effort=low`
- Claude Sonnet 5.5  Medium: `context=300k`, `reasoning_effort=medium`
- Claude Sonnet 5.5  High: `context=300k`, `reasoning_effort=high`
- Claude Sonnet 5.5  Extra High: `context=300k`, `reasoning_effort=xhigh`
- Claude Sonnet 5.5  Max: `context=300k`, `reasoning_effort=max`
- Claude Sonnet 5.5 1M Low: `context=1m`, `reasoning_effort=low`
- Claude Sonnet 5.5 1M Medium: `context=1m`, `reasoning_effort=medium`
- Claude Sonnet 5.5 1M High **(default)**: `context=1m`, `reasoning_effort=high`
- Claude Sonnet 5.5 1M Extra High: `context=1m`, `reasoning_effort=xhigh`
- Claude Sonnet 5.5 1M Max: `context=1m`, `reasoning_effort=max`

#### `composer-2.5`

- **display_name:** Composer 2.5
- **fast_variant:** no
- **aliases:** `composer-latest`, `composer`, `composer-2-5`

**parameters (API):**
- **`fast`** (Fast): `false`, `true (Fast)`

**variants (preset param combinations):**
- Composer 2.5 **(default)**: `fast=true`
- Composer 2.5: `fast=false`

#### `deepseek-v41-flash-1017`

- **display_name:** DeepSeek V4.1 Flash 1017
- **fast_variant:** no

**parameters (API):**
- **`reasoning_effort`** (Reasoning): `none (None)`, `low (Low)`, `medium (Medium)`, `high (High)`, `max (Max)`

**variants (preset param combinations):**
- DeepSeek V4.1 Flash 1017 None: `reasoning_effort=none`
- DeepSeek V4.1 Flash 1017 Low: `reasoning_effort=low`
- DeepSeek V4.1 Flash 1017 Medium: `reasoning_effort=medium`
- DeepSeek V4.1 Flash 1017 High: `reasoning_effort=high`
- DeepSeek V4.1 Flash 1017 Max **(default)**: `reasoning_effort=max`

#### `default`

- **display_name:** Auto
- **fast_variant:** no
- **aliases:** `auto`

**parameters (API):**
- _(нет объявленных parameters в API)_

**variants (preset param combinations):**
- Auto **(default)**: no params

#### `gemini-2.5-flash`

- **display_name:** Gemini 2.5 Flash
- **fast_variant:** no
- **aliases:** `gemini-flash`

**parameters (API):**
- _(нет объявленных parameters в API)_

**variants (preset param combinations):**
- Gemini 2.5 Flash **(default)**: no params

#### `gemini-3-flash`

- **display_name:** Gemini 3 Flash
- **fast_variant:** no

**parameters (API):**
- _(нет объявленных parameters в API)_

**variants (preset param combinations):**
- Gemini 3 Flash **(default)**: no params

#### `gemini-3.1-pro`

- **display_name:** Gemini 3.1 Pro
- **fast_variant:** no
- **aliases:** `gemini-latest`, `gemini-pro-latest`, `gemini`, `gemini-pro`

**parameters (API):**
- _(нет объявленных parameters в API)_

**variants (preset param combinations):**
- Gemini 3.1 Pro **(default)**: no params

#### `gemini-3.5-flash`

- **display_name:** Gemini 3.5 Flash
- **fast_variant:** no
- **aliases:** `gemini-flash-latest`, `gemini-flash`

**parameters (API):**
- _(нет объявленных parameters в API)_

**variants (preset param combinations):**
- Gemini 3.5 Flash **(default)**: no params

#### `gemini-3.6-flash`

- **display_name:** Gemini 3.6 Flash
- **fast_variant:** no
- **aliases:** `gemini-flash-latest`, `gemini-flash`

**parameters (API):**
- **`effort`** (Effort): `minimal (Minimal)`, `low (Low)`, `medium (Medium)`, `high (High)`

**variants (preset param combinations):**
- Gemini 3.6 Flash: `effort=minimal`
- Gemini 3.6 Flash: `effort=low`
- Gemini 3.6 Flash: `effort=medium`
- Gemini 3.6 Flash **(default)**: `effort=high`

#### `gemini-3.7-flash`

- **display_name:** Gemini 3.7 Flash
- **fast_variant:** no
- **aliases:** `gemini-flash-latest`, `gemini-flash`

**parameters (API):**
- **`effort`** (Effort): `low (Low)`, `medium (Medium)`, `high (High)`

**variants (preset param combinations):**
- Gemini 3.7 Flash: `effort=low`
- Gemini 3.7 Flash: `effort=medium`
- Gemini 3.7 Flash **(default)**: `effort=high`

#### `gemini-3.8-flash`

- **display_name:** Gemini 3.8 Flash
- **fast_variant:** no

**parameters (API):**
- **`reasoning_effort`** (Effort): `low (Low)`, `medium (Medium)`, `high (High)`

**variants (preset param combinations):**
- Gemini 3.8 Flash Low: `reasoning_effort=low`
- Gemini 3.8 Flash Medium: `reasoning_effort=medium`
- Gemini 3.8 Flash High **(default)**: `reasoning_effort=high`

#### `gemini-4-argon`

- **display_name:** Gemini 4 Argon
- **fast_variant:** no

**parameters (API):**
- **`reasoning_effort`** (Effort): `low (Low)`, `medium (Medium)`, `high (High)`

**variants (preset param combinations):**
- Gemini 4 Argon Low: `reasoning_effort=low`
- Gemini 4 Argon Medium **(default)**: `reasoning_effort=medium`
- Gemini 4 Argon High: `reasoning_effort=high`

#### `glm-5.2`

- **display_name:** GLM 5.2
- **fast_variant:** no

**parameters (API):**
- **`reasoning`** (Reasoning): `high (High)`, `max (Max)`

**variants (preset param combinations):**
- GLM 5.2 **(default)**: `reasoning=high`
- GLM 5.2: `reasoning=max`

#### `glm-5p3`

- **display_name:** GLM 5.3
- **fast_variant:** no

**parameters (API):**
- **`reasoning`** (Reasoning): `low (Low)`, `high (High)`, `max (Max)`

**variants (preset param combinations):**
- GLM 5.3 Low: `reasoning=low`
- GLM 5.3 High: `reasoning=high`
- GLM 5.3 Max **(default)**: `reasoning=max`

#### `glm-5p3-flash`

- **display_name:** GLM 5.3 Flash
- **fast_variant:** no

**parameters (API):**
- **`reasoning`** (Reasoning): `low (Low)`, `high (High)`, `max (Max)`

**variants (preset param combinations):**
- GLM 5.3 Flash Low: `reasoning=low`
- GLM 5.3 Flash High: `reasoning=high`
- GLM 5.3 Flash Max **(default)**: `reasoning=max`

#### `gpt-5-mini`

- **display_name:** GPT-5 Mini
- **fast_variant:** no
- **aliases:** `gpt-mini`

**parameters (API):**
- _(нет объявленных parameters в API)_

**variants (preset param combinations):**
- GPT-5 Mini **(default)**: no params

#### `gpt-5.1`

- **display_name:** GPT-5.1
- **fast_variant:** no
- **aliases:** `gpt`

**parameters (API):**
- **`reasoning`** (Reasoning): `low (Low)`, `medium (Medium)`, `high (High)`

**variants (preset param combinations):**
- GPT-5.1: `reasoning=low`
- GPT-5.1 **(default)**: `reasoning=medium`
- GPT-5.1: `reasoning=high`

#### `gpt-5.2`

- **display_name:** GPT-5.2
- **fast_variant:** no
- **aliases:** `gpt`

**parameters (API):**
- **`reasoning`** (Reasoning): `low (Low)`, `medium (Medium)`, `high (High)`, `extra-high (Extra High)`
- **`fast`** (Fast): `false`, `true (Fast)`

**variants (preset param combinations):**
- GPT-5.2: `reasoning=low`, `fast=false`
- GPT-5.2: `reasoning=low`, `fast=true`
- GPT-5.2: `reasoning=medium`, `fast=false`
- GPT-5.2: `reasoning=medium`, `fast=true`
- GPT-5.2: `reasoning=high`, `fast=false`
- GPT-5.2 **(default)**: `reasoning=high`, `fast=true`
- GPT-5.2: `reasoning=extra-high`, `fast=false`
- GPT-5.2: `reasoning=extra-high`, `fast=true`

#### `gpt-5.3-codex`

- **display_name:** Codex 5.3
- **fast_variant:** no
- **aliases:** `codex-latest`, `codex`, `codex-5.3`

**parameters (API):**
- **`reasoning`** (Reasoning): `low (Low)`, `medium (Medium)`, `high (High)`, `extra-high (Extra High)`
- **`fast`** (Fast): `false`, `true (Fast)`

**variants (preset param combinations):**
- Codex 5.3: `reasoning=low`, `fast=false`
- Codex 5.3: `reasoning=low`, `fast=true`
- Codex 5.3: `reasoning=medium`, `fast=false`
- Codex 5.3: `reasoning=medium`, `fast=true`
- Codex 5.3: `reasoning=high`, `fast=false`
- Codex 5.3 **(default)**: `reasoning=high`, `fast=true`
- Codex 5.3: `reasoning=extra-high`, `fast=false`
- Codex 5.3: `reasoning=extra-high`, `fast=true`

#### `gpt-5.4`

- **display_name:** GPT-5.4
- **fast_variant:** no
- **aliases:** `gpt`

**parameters (API):**
- **`context`** (Context): `272k (272K)`, `1m (1M)`
- **`reasoning`** (Reasoning): `none (None)`, `low (Low)`, `medium (Medium)`, `high (High)`, `extra-high (Extra High)`
- **`fast`** (Fast): `false`, `true (Fast)`

**variants (preset param combinations):**
- GPT-5.4: `context=272k`, `reasoning=none`, `fast=false`
- GPT-5.4: `context=272k`, `reasoning=none`, `fast=true`
- GPT-5.4: `context=272k`, `reasoning=low`, `fast=false`
- GPT-5.4: `context=272k`, `reasoning=low`, `fast=true`
- GPT-5.4: `context=272k`, `reasoning=medium`, `fast=false`
- GPT-5.4: `context=272k`, `reasoning=medium`, `fast=true`
- GPT-5.4: `context=272k`, `reasoning=high`, `fast=false`
- GPT-5.4: `context=272k`, `reasoning=high`, `fast=true`
- GPT-5.4: `context=272k`, `reasoning=extra-high`, `fast=false`
- GPT-5.4: `context=272k`, `reasoning=extra-high`, `fast=true`
- GPT-5.4: `context=1m`, `reasoning=none`, `fast=false`
- GPT-5.4: `context=1m`, `reasoning=low`, `fast=false`
- GPT-5.4 **(default)**: `context=1m`, `reasoning=medium`, `fast=false`
- GPT-5.4: `context=1m`, `reasoning=high`, `fast=false`
- GPT-5.4: `context=1m`, `reasoning=extra-high`, `fast=false`

#### `gpt-5.4-mini`

- **display_name:** GPT-5.4 Mini
- **fast_variant:** no
- **aliases:** `gpt-mini-latest`, `gpt-mini`

**parameters (API):**
- **`reasoning`** (Reasoning): `none (None)`, `low (Low)`, `medium (Medium)`, `high (High)`, `xhigh (Extra High)`

**variants (preset param combinations):**
- GPT-5.4 Mini: `reasoning=none`
- GPT-5.4 Mini: `reasoning=low`
- GPT-5.4 Mini **(default)**: `reasoning=medium`
- GPT-5.4 Mini: `reasoning=high`
- GPT-5.4 Mini: `reasoning=xhigh`

#### `gpt-5.4-nano`

- **display_name:** GPT-5.4 Nano
- **fast_variant:** no
- **aliases:** `gpt-nano-latest`, `gpt-nano`

**parameters (API):**
- **`reasoning`** (Reasoning): `none (None)`, `low (Low)`, `medium (Medium)`, `high (High)`, `xhigh (Extra High)`

**variants (preset param combinations):**
- GPT-5.4 Nano: `reasoning=none`
- GPT-5.4 Nano: `reasoning=low`
- GPT-5.4 Nano **(default)**: `reasoning=medium`
- GPT-5.4 Nano: `reasoning=high`
- GPT-5.4 Nano: `reasoning=xhigh`

#### `gpt-5.5`

- **display_name:** GPT-5.5
- **fast_variant:** no
- **aliases:** `gpt-5-5`

**parameters (API):**
- **`context`** (Context): `272k (272K)`, `1m (1M)`
- **`reasoning`** (Reasoning): `none (None)`, `low (Low)`, `medium (Medium)`, `high (High)`, `extra-high (Extra High)`
- **`fast`** (Fast): `false`, `true (Fast)`

**variants (preset param combinations):**
- GPT-5.5: `context=272k`, `reasoning=none`, `fast=false`
- GPT-5.5: `context=272k`, `reasoning=none`, `fast=true`
- GPT-5.5: `context=272k`, `reasoning=low`, `fast=false`
- GPT-5.5: `context=272k`, `reasoning=low`, `fast=true`
- GPT-5.5: `context=272k`, `reasoning=medium`, `fast=false`
- GPT-5.5: `context=272k`, `reasoning=medium`, `fast=true`
- GPT-5.5: `context=272k`, `reasoning=high`, `fast=false`
- GPT-5.5: `context=272k`, `reasoning=high`, `fast=true`
- GPT-5.5: `context=272k`, `reasoning=extra-high`, `fast=false`
- GPT-5.5: `context=272k`, `reasoning=extra-high`, `fast=true`
- GPT-5.5: `context=1m`, `reasoning=none`, `fast=false`
- GPT-5.5: `context=1m`, `reasoning=low`, `fast=false`
- GPT-5.5 **(default)**: `context=1m`, `reasoning=medium`, `fast=false`
- GPT-5.5: `context=1m`, `reasoning=high`, `fast=false`
- GPT-5.5: `context=1m`, `reasoning=extra-high`, `fast=false`

#### `gpt-5.6-luna`

- **display_name:** GPT-5.6 Luna
- **fast_variant:** no
- **aliases:** `gpt-5-6-luna`

**parameters (API):**
- **`context`** (Context): `272k (272K)`, `1m (1M)`
- **`reasoning`** (Reasoning): `none (None)`, `low (Low)`, `medium (Medium)`, `high (High)`, `xhigh (Extra High)`, `max (Max)`
- **`fast`** (Fast): `false`, `true (Fast)`

**variants (preset param combinations):**
- GPT-5.6 Luna: `context=272k`, `reasoning=none`, `fast=false`
- GPT-5.6 Luna: `context=272k`, `reasoning=none`, `fast=true`
- GPT-5.6 Luna: `context=272k`, `reasoning=low`, `fast=false`
- GPT-5.6 Luna: `context=272k`, `reasoning=low`, `fast=true`
- GPT-5.6 Luna: `context=272k`, `reasoning=medium`, `fast=false`
- GPT-5.6 Luna: `context=272k`, `reasoning=medium`, `fast=true`
- GPT-5.6 Luna: `context=272k`, `reasoning=high`, `fast=false`
- GPT-5.6 Luna: `context=272k`, `reasoning=high`, `fast=true`
- GPT-5.6 Luna: `context=272k`, `reasoning=xhigh`, `fast=false`
- GPT-5.6 Luna: `context=272k`, `reasoning=xhigh`, `fast=true`
- GPT-5.6 Luna: `context=272k`, `reasoning=max`, `fast=false`
- GPT-5.6 Luna: `context=272k`, `reasoning=max`, `fast=true`
- GPT-5.6 Luna: `context=1m`, `reasoning=none`, `fast=false`
- GPT-5.6 Luna: `context=1m`, `reasoning=none`, `fast=true`
- GPT-5.6 Luna: `context=1m`, `reasoning=low`, `fast=false`
- GPT-5.6 Luna: `context=1m`, `reasoning=low`, `fast=true`
- GPT-5.6 Luna **(default)**: `context=1m`, `reasoning=medium`, `fast=false`
- GPT-5.6 Luna: `context=1m`, `reasoning=medium`, `fast=true`
- GPT-5.6 Luna: `context=1m`, `reasoning=high`, `fast=false`
- GPT-5.6 Luna: `context=1m`, `reasoning=high`, `fast=true`
- GPT-5.6 Luna: `context=1m`, `reasoning=xhigh`, `fast=false`
- GPT-5.6 Luna: `context=1m`, `reasoning=xhigh`, `fast=true`
- GPT-5.6 Luna: `context=1m`, `reasoning=max`, `fast=false`
- GPT-5.6 Luna: `context=1m`, `reasoning=max`, `fast=true`

#### `gpt-5.6-sol`

- **display_name:** GPT-5.6 Sol
- **fast_variant:** no
- **aliases:** `gpt-latest`, `gpt`, `gpt-5-6-sol`, `gpt-5.6`

**parameters (API):**
- **`context`** (Context): `272k (272K)`, `1m (1M)`
- **`reasoning`** (Reasoning): `none (None)`, `low (Low)`, `medium (Medium)`, `high (High)`, `xhigh (Extra High)`, `max (Max)`
- **`fast`** (Fast): `false`, `true (Fast)`

**variants (preset param combinations):**
- GPT-5.6 Sol: `context=272k`, `reasoning=none`, `fast=false`
- GPT-5.6 Sol: `context=272k`, `reasoning=none`, `fast=true`
- GPT-5.6 Sol: `context=272k`, `reasoning=low`, `fast=false`
- GPT-5.6 Sol: `context=272k`, `reasoning=low`, `fast=true`
- GPT-5.6 Sol: `context=272k`, `reasoning=medium`, `fast=false`
- GPT-5.6 Sol: `context=272k`, `reasoning=medium`, `fast=true`
- GPT-5.6 Sol: `context=272k`, `reasoning=high`, `fast=false`
- GPT-5.6 Sol: `context=272k`, `reasoning=high`, `fast=true`
- GPT-5.6 Sol: `context=272k`, `reasoning=xhigh`, `fast=false`
- GPT-5.6 Sol: `context=272k`, `reasoning=xhigh`, `fast=true`
- GPT-5.6 Sol: `context=272k`, `reasoning=max`, `fast=false`
- GPT-5.6 Sol: `context=272k`, `reasoning=max`, `fast=true`
- GPT-5.6 Sol: `context=1m`, `reasoning=none`, `fast=false`
- GPT-5.6 Sol: `context=1m`, `reasoning=none`, `fast=true`
- GPT-5.6 Sol: `context=1m`, `reasoning=low`, `fast=false`
- GPT-5.6 Sol: `context=1m`, `reasoning=low`, `fast=true`
- GPT-5.6 Sol **(default)**: `context=1m`, `reasoning=medium`, `fast=false`
- GPT-5.6 Sol: `context=1m`, `reasoning=medium`, `fast=true`
- GPT-5.6 Sol: `context=1m`, `reasoning=high`, `fast=false`
- GPT-5.6 Sol: `context=1m`, `reasoning=high`, `fast=true`
- GPT-5.6 Sol: `context=1m`, `reasoning=xhigh`, `fast=false`
- GPT-5.6 Sol: `context=1m`, `reasoning=xhigh`, `fast=true`
- GPT-5.6 Sol: `context=1m`, `reasoning=max`, `fast=false`
- GPT-5.6 Sol: `context=1m`, `reasoning=max`, `fast=true`

#### `gpt-5.6-terra`

- **display_name:** GPT-5.6 Terra
- **fast_variant:** no
- **aliases:** `gpt-5-6-terra`

**parameters (API):**
- **`context`** (Context): `272k (272K)`, `1m (1M)`
- **`reasoning`** (Reasoning): `none (None)`, `low (Low)`, `medium (Medium)`, `high (High)`, `xhigh (Extra High)`, `max (Max)`
- **`fast`** (Fast): `false`, `true (Fast)`

**variants (preset param combinations):**
- GPT-5.6 Terra: `context=272k`, `reasoning=none`, `fast=false`
- GPT-5.6 Terra: `context=272k`, `reasoning=none`, `fast=true`
- GPT-5.6 Terra: `context=272k`, `reasoning=low`, `fast=false`
- GPT-5.6 Terra: `context=272k`, `reasoning=low`, `fast=true`
- GPT-5.6 Terra: `context=272k`, `reasoning=medium`, `fast=false`
- GPT-5.6 Terra: `context=272k`, `reasoning=medium`, `fast=true`
- GPT-5.6 Terra: `context=272k`, `reasoning=high`, `fast=false`
- GPT-5.6 Terra: `context=272k`, `reasoning=high`, `fast=true`
- GPT-5.6 Terra: `context=272k`, `reasoning=xhigh`, `fast=false`
- GPT-5.6 Terra: `context=272k`, `reasoning=xhigh`, `fast=true`
- GPT-5.6 Terra: `context=272k`, `reasoning=max`, `fast=false`
- GPT-5.6 Terra: `context=272k`, `reasoning=max`, `fast=true`
- GPT-5.6 Terra: `context=1m`, `reasoning=none`, `fast=false`
- GPT-5.6 Terra: `context=1m`, `reasoning=none`, `fast=true`
- GPT-5.6 Terra: `context=1m`, `reasoning=low`, `fast=false`
- GPT-5.6 Terra: `context=1m`, `reasoning=low`, `fast=true`
- GPT-5.6 Terra **(default)**: `context=1m`, `reasoning=medium`, `fast=false`
- GPT-5.6 Terra: `context=1m`, `reasoning=medium`, `fast=true`
- GPT-5.6 Terra: `context=1m`, `reasoning=high`, `fast=false`
- GPT-5.6 Terra: `context=1m`, `reasoning=high`, `fast=true`
- GPT-5.6 Terra: `context=1m`, `reasoning=xhigh`, `fast=false`
- GPT-5.6 Terra: `context=1m`, `reasoning=xhigh`, `fast=true`
- GPT-5.6 Terra: `context=1m`, `reasoning=max`, `fast=false`
- GPT-5.6 Terra: `context=1m`, `reasoning=max`, `fast=true`

#### `grok-4.5`

- **display_name:** Grok 4.5
- **fast_variant:** no

**parameters (API):**
- **`effort`** (Effort): `low (Low)`, `medium (Medium)`, `high (High)`
- **`fast`** (Fast): `false`, `true (Fast​)`

**variants (preset param combinations):**
- Grok 4.5: `effort=low`, `fast=false`
- Grok 4.5: `effort=low`, `fast=true`
- Grok 4.5: `effort=medium`, `fast=false`
- Grok 4.5: `effort=medium`, `fast=true`
- Grok 4.5: `effort=high`, `fast=false`
- Grok 4.5 **(default)**: `effort=high`, `fast=true`

#### `grok-4.6`

- **display_name:** Grok 4.6
- **fast_variant:** no

**parameters (API):**
- **`effort`** (Effort): `low (Low)`, `medium (Medium)`, `high (High)`, `xhigh (Extra High)`
- **`fast`** (Fast): `false`, `true (Fast​​)`

**variants (preset param combinations):**
- Grok 4.6: `effort=low`, `fast=false`
- Grok 4.6: `effort=low`, `fast=true`
- Grok 4.6: `effort=medium`, `fast=false`
- Grok 4.6: `effort=medium`, `fast=true`
- Grok 4.6: `effort=high`, `fast=false`
- Grok 4.6 **(default)**: `effort=high`, `fast=true`
- Grok 4.6: `effort=xhigh`, `fast=false`
- Grok 4.6: `effort=xhigh`, `fast=true`

#### `grok-4.7`

- **display_name:** Grok 4.7
- **fast_variant:** no

**parameters (API):**
- **`context`** (Context): `256k (256K)`, `500k (500K)`
- **`reasoning_effort`** (Effort): `low (Low)`, `medium (Medium)`, `high (High)`, `xhigh (Extra High)`
- **`fast`** (Fast): `false`, `true (Fast​​)`

**variants (preset param combinations):**
- Grok 4.7  Low: `context=256k`, `reasoning_effort=low`, `fast=false`
- Grok 4.7  Low Fast​​: `context=256k`, `reasoning_effort=low`, `fast=true`
- Grok 4.7  Medium: `context=256k`, `reasoning_effort=medium`, `fast=false`
- Grok 4.7  Medium Fast​​: `context=256k`, `reasoning_effort=medium`, `fast=true`
- Grok 4.7  High: `context=256k`, `reasoning_effort=high`, `fast=false`
- Grok 4.7  High Fast​​: `context=256k`, `reasoning_effort=high`, `fast=true`
- Grok 4.7  Extra High: `context=256k`, `reasoning_effort=xhigh`, `fast=false`
- Grok 4.7  Extra High Fast​​: `context=256k`, `reasoning_effort=xhigh`, `fast=true`
- Grok 4.7  Low: `context=500k`, `reasoning_effort=low`, `fast=false`
- Grok 4.7  Low Fast​​: `context=500k`, `reasoning_effort=low`, `fast=true`
- Grok 4.7  Medium: `context=500k`, `reasoning_effort=medium`, `fast=false`
- Grok 4.7  Medium Fast​​: `context=500k`, `reasoning_effort=medium`, `fast=true`
- Grok 4.7  High: `context=500k`, `reasoning_effort=high`, `fast=false`
- Grok 4.7  High Fast​​ **(default)**: `context=500k`, `reasoning_effort=high`, `fast=true`
- Grok 4.7  Extra High: `context=500k`, `reasoning_effort=xhigh`, `fast=false`
- Grok 4.7  Extra High Fast​​: `context=500k`, `reasoning_effort=xhigh`, `fast=true`

#### `kimi-k2.7-code`

- **display_name:** Kimi K2.7 Code
- **fast_variant:** no

**parameters (API):**
- _(нет объявленных parameters в API)_

**variants (preset param combinations):**
- Kimi K2.7 Code **(default)**: no params

#### `kimi-k3`

- **display_name:** Kimi K3
- **fast_variant:** no

**parameters (API):**
- **`reasoning`** (Reasoning): `low (Low)`, `high (High)`, `max (Max)`

**variants (preset param combinations):**
- Kimi K3: `reasoning=low`
- Kimi K3: `reasoning=high`
- Kimi K3 **(default)**: `reasoning=max`

#### `muse-spark-1.3`

- **display_name:** Muse Spark 1.3
- **fast_variant:** no
- **aliases:** `muse-spark`, `muse`

**parameters (API):**
- **`context`** (Context): `300k (300K)`, `1m (1M)`
- **`effort`** (Effort): `minimal (Minimal)`, `low (Low)`, `medium (Medium)`, `high (High)`, `xhigh (Extra High)`, `max (Max)`

**variants (preset param combinations):**
- Muse Spark 1.3: `context=300k`, `effort=minimal`
- Muse Spark 1.3: `context=300k`, `effort=low`
- Muse Spark 1.3: `context=300k`, `effort=medium`
- Muse Spark 1.3: `context=300k`, `effort=high`
- Muse Spark 1.3: `context=300k`, `effort=xhigh`
- Muse Spark 1.3: `context=300k`, `effort=max`
- Muse Spark 1.3: `context=1m`, `effort=minimal`
- Muse Spark 1.3: `context=1m`, `effort=low`
- Muse Spark 1.3: `context=1m`, `effort=medium`
- Muse Spark 1.3 **(default)**: `context=1m`, `effort=high`
- Muse Spark 1.3: `context=1m`, `effort=xhigh`
- Muse Spark 1.3: `context=1m`, `effort=max`

#### `muse-wonder-1.0`

- **display_name:** Muse Wonder
- **fast_variant:** no

**parameters (API):**
- **`context`** (Context): `300k (300K)`, `1m (1M)`
- **`reasoning_effort`** (Reasoning): `minimal (Minimal)`, `low (Low)`, `medium (Medium)`, `high (High)`, `xhigh (Extra High)`, `max (Max)`

**variants (preset param combinations):**
- Muse Wonder  Minimal: `context=300k`, `reasoning_effort=minimal`
- Muse Wonder  Low: `context=300k`, `reasoning_effort=low`
- Muse Wonder  Medium: `context=300k`, `reasoning_effort=medium`
- Muse Wonder  High: `context=300k`, `reasoning_effort=high`
- Muse Wonder  Extra High: `context=300k`, `reasoning_effort=xhigh`
- Muse Wonder  Max: `context=300k`, `reasoning_effort=max`
- Muse Wonder 1M Minimal: `context=1m`, `reasoning_effort=minimal`
- Muse Wonder 1M Low: `context=1m`, `reasoning_effort=low`
- Muse Wonder 1M Medium: `context=1m`, `reasoning_effort=medium`
- Muse Wonder 1M High: `context=1m`, `reasoning_effort=high`
- Muse Wonder 1M Extra High **(default)**: `context=1m`, `reasoning_effort=xhigh`
- Muse Wonder 1M Max: `context=1m`, `reasoning_effort=max`

