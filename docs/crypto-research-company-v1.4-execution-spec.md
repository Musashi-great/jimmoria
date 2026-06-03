# 멀티에이전트 크립토 리서칭 회사 v1.4 실행 스펙

v1.3은 기획/아키텍처 문서이고, v1.4는 개발 가능한 실행 스펙이다.

## 1. MVP 우선순위

초기 MVP는 Telegram Bot이 아니라 CLI-first로 운용한다. Telegram Bot은 나중에 같은 런타임을 호출하는 입력 채널로 붙인다.

```text
MVP Loop 1: CLI Article Ingestion Loop
1. CLI에서 URL/file/text 입력
2. Supervisor가 Research Room 생성
3. Ingestion Agent가 Source 저장, 엔티티/키워드 추출
4. Narrative Agent가 핵심 내러티브 분류
5. Discovery Agent가 유사 초기 프로젝트 후보 생성
6. Report Agent가 Candidate Dossier 생성
7. Obsidian Curator가 Source / Project / Report Note 저장
```

이후 확장 순서:

```text
MVP Loop 2: Project Research Loop
MVP Loop 3: Daily Radar Loop
```

## 2. Phase 0 고정 스키마

개발 전에 다음 8개 스키마를 고정한다.

```text
1. ResearchRoom
2. AgentMessage
3. AgentSpec
4. AgentFinding
5. ToolCall
6. Source
7. ReportTemplate
8. ObsidianNoteTemplate
```

현재 MVP 코드 매핑:

```text
ResearchRoom       -> crypto_research_agents/core/room.py
AgentMessage       -> crypto_research_agents/core/message.py
AgentSpec          -> crypto_research_agents/core/agent_spec.py + config/agents/*.yaml
AgentFinding       -> crypto_research_agents/core/memory.py: FindingRecord
ToolCall           -> crypto_research_agents/core/tool_call.py
Source             -> crypto_research_agents/core/memory.py: SourceRecord
ReportTemplate     -> templates/reports/*.md
ObsidianTemplate   -> templates/obsidian/*.md
```

## 3. Runtime State Machine

Research Room과 Agent Task는 같은 상태 언어를 사용한다.

```text
created
-> assigned
-> running
-> waiting_for_tool
-> waiting_for_agent
-> ready_for_report
-> writing_report
-> obsidian_syncing
-> completed
-> failed
```

MVP에서는 Room 단위 상태만 사용하고, V1에서 Agent Task 상태를 별도로 분리한다.

## 4. 최소 DB 스키마

현재는 JSON 저장소를 사용하지만, Postgres로 이전할 때의 기준 스키마는 다음과 같다.

```sql
research_rooms (
  id text primary key,
  topic text not null,
  goal jsonb not null,
  status text not null,
  created_by text,
  created_at timestamptz not null,
  updated_at timestamptz
);

agent_messages (
  id text primary key,
  room_id text not null,
  from_agent text not null,
  to_agent text not null,
  type text not null,
  priority text not null,
  payload jsonb not null,
  status text not null,
  created_at timestamptz not null
);

agent_findings (
  id text primary key,
  room_id text not null,
  agent_id text not null,
  project_id text,
  finding_type text not null,
  summary text not null,
  data jsonb not null,
  confidence numeric,
  source_ids jsonb not null,
  created_at timestamptz not null
);

tool_calls (
  id text primary key,
  room_id text,
  agent_id text not null,
  tool_name text not null,
  input jsonb not null,
  status text not null,
  result_ref text,
  source_id text,
  latency_ms integer,
  cost_usd numeric,
  created_at timestamptz not null
);

sources (
  id text primary key,
  source_type text not null,
  title text not null,
  url text,
  author text,
  published_at timestamptz,
  raw_path text,
  summary text,
  entities jsonb,
  narratives jsonb,
  created_at timestamptz not null
);

projects (
  id text primary key,
  name text not null,
  ticker text,
  chain text,
  website text,
  x_account text,
  telegram text,
  docs text,
  github text,
  token_status text,
  contract_status text,
  research_status text,
  early_radar_score numeric,
  created_at timestamptz not null,
  updated_at timestamptz
);
```

## 5. ToolCall 표준

모든 외부 툴 호출은 다음 구조로 기록한다.

```yaml
tool_call_id: toolcall_001
room_id: room_abc
agent_id: contract_onchain_agent
tool_name: explorer_lookup
input:
  chain: Base
  query: Project ABC contract
status: success
result_ref: result_001
source_id: src_010
latency_ms: 820
cost_usd: 0.001
created_at: "2026-06-02T00:00:00Z"
```

이 로그는 다음 목적으로 사용한다.

```text
- API 실패 추적
- Agent별 비용 추적
- 보고서 근거 추적
- 캐시 재사용
- 권한/보안 감사
```

## 6. Agent Finding 표준

최종 보고서보다 더 중요한 데이터는 Agent Finding이다.

```yaml
finding_id: finding_001
room_id: room_abc
agent_id: social_kol_agent
project_id: project_abc
finding_type: social_momentum
summary: "최근 X에서 AI wallet 관련 언급이 증가했고, 특정 KOL 3명이 반복 언급함."
confidence: 72
data:
  key_accounts:
    - "@kol1"
    - "@kol2"
  mention_trend: up
  community_signal: moderate
source_ids:
  - src_001
  - src_002
created_at: "2026-06-02T00:00:00Z"
```

Report Agent는 외부 데이터를 직접 해석하기보다 Agent Finding을 조립해야 한다.

## 7. CLI-first 운영 명령

현재 MVP의 기본 입력 채널은 CLI다.

```powershell
jimmoria
jimmoria add-source --title "Source" --url "https://..."
jimmoria research --title "Project thesis" --file .\source.txt
jimmoria runs
jimmoria doctor
jimmoria status <room_id>
jimmoria messages <room_id> --limit 10
jimmoria show-report <room_id>
```

명령 역할:

```text
chat        -> 대화형 운영 콘솔. 입력마다 Research Room 실행 과정을 표시
add-source  -> Source 저장 + Obsidian Source Note 생성
research    -> 전체 Research Room / Agent Collaboration / Report / Obsidian 루프 실행
runs        -> 과거 실행 목록 조회
status      -> 특정 Research Room 요약 조회
messages    -> Agent Collaboration Bus 메시지 조회
show-report -> 저장된 markdown 보고서 출력
doctor      -> 실제 연결된 기능과 placeholder 기능 확인
```

Chat mode는 사용자가 질문을 입력하면 다음 이벤트를 즉시 출력한다.

```text
room_created
agent_start
agent_done
room_completed
```

따라서 사용자는 `Supervisor`, `Ingestion`, `Narrative`, `Discovery`, `Social/KOL`, `Contract`, `Product/Tech`, `Funding/Token`, `Report`, `Obsidian Curator` 중 누가 어떤 단계에서 일하는지 CLI에서 볼 수 있다.

현재 MVP에서 실제로 동작하는 것:

```text
Research Room 생성
Agent Collaboration Bus 메시지 기록
AgentSpec/persona 로딩
LLM Provider 라우팅 및 fallback
Report markdown 생성
Obsidian-style note 생성
Run snapshot 저장
```

현재 placeholder인 것:

```text
X/Twitter search
Telegram/Discord read
GitHub/Docs live crawler
Explorer/RPC/DEX connector
Funding/airdrop live checker
```

Chat mode 시작 시 모델 설정 패널을 보여준다.

```text
1. Codex SDK / local app-server (Recommended)
2. Codex CLI exec
3. Offline diagnostic fallback
```

Current CLI behavior:

```text
Codex SDK install: pip install openai-codex
Codex CLI login fallback: codex login --device-auth
Login persistence: reused until sign-out, machine change, or session expiry
Saved local settings: data/model_settings.json
Recommended model route: Use provider default for every agent
Advanced model route: fixed Codex model list only
```

JIMMORIA가 지원하는 Codex 모델 route는 다음 값으로 고정한다.

```text
CODEX_MODEL_FAST_CHAT   default gpt-5.4-mini
CODEX_MODEL_FAST        default gpt-5.4-mini
CODEX_MODEL_REASONING   default gpt-5.5
CODEX_MODEL_WRITING     default gpt-5.5
CODEX_MODEL_STRONG      default gpt-5.5
```

토큰이나 API key는 config 파일에 저장하지 않는다. 저장하는 것은 provider/model preference뿐이다.

## 11. Agent Persona Layer

v1.4부터 AgentSpec은 역할/권한뿐 아니라 페르소나도 포함한다.

```text
Agent = Role + Goal + Tools + Memory Scope + Skills + Hooks + Output Schema + Model Policy + Persona
```

공통 원칙:

```text
1. 전문 영역 밖의 판단은 하지 않는다.
2. 모르는 정보는 Unknown / Unclear로 표시한다.
3. 모든 외부 정보는 Source ID와 함께 남긴다.
4. 매매 신호가 아니라 프로젝트/내러티브 리서치 품질을 최적화한다.
5. Report Agent와 Obsidian Curator가 재사용 가능한 Finding을 남긴다.
```

페르소나 필드:

```text
persona_name
persona_strength
identity
personality
mission
scope
must_follow
must_not
```

핵심 페르소나:

```text
Supervisor           -> The Research Director
Ingestion            -> The Archivist
Discovery            -> The Scout
Social/KOL           -> The Signal Listener
Contract/On-chain    -> The Chain Verifier
Product/Tech         -> The Product Analyst
Narrative            -> The Thesis Mapper
Funding/Token        -> The Token Opportunity Analyst
Report               -> The Research Editor
Obsidian Curator     -> The Knowledge Curator
24H Monitor          -> The Night Watcher
Memory/Retrieval     -> The Memory Librarian
Tool/Policy          -> The Gatekeeper
```

LLM 호출 시 `AgentSpec.system_prompt()`가 system prompt로 사용된다. 따라서 같은 모델을 써도 에이전트별 사고방식, 말투, 판단 기준, 금지 범위가 분리된다.

## 8. MVP 기술 스택

추천 최종 스택:

```text
Backend: Python FastAPI
Agent Runtime: Lightweight state machine first, LangGraph optional later
Queue: Redis Queue / Celery / Dramatiq
Scheduler: APScheduler / Celery Beat
DB: Postgres
Vector DB: pgvector
Storage: Local storage first, S3-compatible later
Obsidian: Local Vault folder markdown write
Telegram: python-telegram-bot
Dashboard: later Next.js
```

현재 코드 상태:

```text
Backend: Python CLI MVP
Storage: JSON + local reports + local vault
Agent Runtime: Lightweight sequential state machine
Tool Gateway: permission checked stub
```

## 9. 다음 구현 순서

```text
1. AgentSpec YAML 고정
2. ToolCall 표준 로그 저장
3. 템플릿 기반 보고서/Obsidian 작성
4. URL fetcher / RSS connector
5. 실제 Codex SDK/Codex CLI LLM Provider 연결
6. X/Twitter, Telegram, GitHub, Explorer connector
7. Postgres + pgvector 전환
8. Telegram Bot 입력 채널
```

## 10. LLM Provider 연결

v1.4 코드에서는 `ModelGateway`가 실제 LLM Provider를 호출한다.

```text
Agent
-> ModelGateway
-> Model Router
-> LLMProvider
-> CodexSdkProvider / CodexCliProvider / OfflineLLMProvider
-> Agent
```

현재 구현:

```text
CodexSdkProvider
- LLM_PROVIDER=codex_sdk
- 공식 openai-codex Python SDK 사용
- 로컬 Codex app-server를 JSON-RPC로 제어
- Codex thread_start(model=..., sandbox=...) 후 thread.run(prompt)
- 기본 sandbox는 read_only

CodexCliProvider
- LLM_PROVIDER=codex_cli
- Uses the local Codex CLI ChatGPT login session
- Login is created with codex login --device-auth
- Model route can stay on provider default; exact model ids must be from the fixed Codex list

OfflineLLMProvider
- 라이브 모델이 없을 때 deterministic fallback
- 테스트와 로컬 개발이 끊기지 않게 함
```

환경 변수:

```powershell
$env:LLM_PROVIDER="codex_sdk"
$env:CODEX_SDK_SANDBOX="read_only"
$env:CODEX_MODEL_REASONING="gpt-5.5"
$env:CODEX_MODEL_WRITING="gpt-5.5"
$env:CODEX_MODEL_FAST="gpt-5.4-mini"
```

Codex CLI fallback:

```powershell
$env:LLM_PROVIDER="codex_cli"
$env:CODEX_CLI_MODEL_REASONING="gpt-5.5"
$env:CODEX_CLI_MODEL_WRITING="gpt-5.5"
$env:CODEX_CLI_MODEL_FAST="gpt-5.4-mini"
```

OpenAI API Key와 임의 OAuth bearer token provider는 지원 provider 경로에서 제거했다.

현재 LLM을 사용하는 에이전트:

```text
Ingestion Agent
- source summary
- entities
- keywords

Narrative Agent
- narrative classification
- rationale

Report Agent
- final synthesis / TL;DR
```

모든 호출은 다음 파일에 저장된다.

```text
data/runs/<room_id>/llm_call_log.json
```

설계 원칙:

```text
1. Agent는 provider API key를 직접 알지 않는다.
2. Agent는 task_type과 prompt를 ModelGateway에 보낸다.
3. ModelGateway가 모델을 선택하고 호출한다.
4. LLM 실패 또는 미설정 시 fallback이 작동한다.
5. LLM 결과는 Agent Finding과 Source 기반 deterministic 결과로 보완한다.
```
