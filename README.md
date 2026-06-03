# JIMMORIA

JIMMORIA는 크립토 리서치 전용 멀티에이전트 회사 CLI입니다.

사용자는 터미널에서 채팅을 치고, Supervisor가 Research Room을 열어 여러 에이전트에게 일을 나눕니다. 에이전트들은 소스 정리, 내러티브 분석, 후보 프로젝트 발굴, KOL/소셜 체크, 온체인/제품/토큰 체크, 보고서 작성, Obsidian 노트 정리를 담당합니다.

현재는 MVP입니다. 에이전트 협업 구조와 보고서 생성 흐름은 동작하고, Supervisor Office가 Research Room 업무를 만들고 하위 에이전트에게 배정합니다. 하위 에이전트가 수행을 마치면 Agent Council이 findings를 모아 consensus를 만들고, ReportAgent가 작성한 뒤 Supervisor가 최종 검토해서 전달 모드를 결정합니다. Web Search/URL/Website/Docs/GitHub/DEX Screener/CoinGecko 기본 connector도 ToolGateway 뒤에 붙어 있습니다. X/Twitter, Telegram, RootData, Explorer/RPC, funding/airdrop connector도 등록되어 있고, API key나 대상 입력이 없을 때는 `missing_secret` 또는 `missing_input`으로 표시됩니다. Discord, Dune, The Graph 등은 아직 별도 connector가 필요한 상태입니다.

## Quick Start

처음 한 번만 설치합니다.

```powershell
cd C:\jimmoria
python -m pip install -e ".[all]"
```

이후에는 바로 실행합니다.

```powershell
jimmoria
```

웹 대시보드:

웹에서 회사 구조와 Research Room 실행 기록을 확인하려면 로컬 Web Research HQ를 띄웁니다.

```powershell
jimmoria web
```

기본 주소는 `http://127.0.0.1:8787`입니다. 이 대시보드는 `data/runs`, `reports`, `vault`를 읽어서 Supervisor, Research Room, Agent Board, Agent Council, final review, report preview, replay events를 한 화면에 보여줍니다. 브라우저를 자동으로 열고 싶지 않으면 `jimmoria web --no-browser`를 사용합니다.

데모 실행:

```powershell
jimmoria demo
```

## Model Setup

처음 실행하면 모델 선택 화면이 나옵니다.

```text
1. Codex SDK / local app-server (Recommended)
2. Codex CLI exec
3. Offline diagnostic fallback
```

추천 흐름은 `Codex SDK / local app-server`입니다. 공식 Codex SDK는 `openai-codex` 패키지로 설치하며, 로컬 Codex app-server를 통해 thread를 만들고 실행합니다.

```powershell
python -m pip install -e ".[codex]"
```

ChatGPT/Codex 로그인이 되어 있으면 보통 다시 로그인할 필요가 없습니다. 로그아웃하거나, 다른 컴퓨터로 옮기거나, 세션이 만료된 경우에만 다시 로그인하면 됩니다.

JIMMORIA는 토큰이나 API key를 저장하지 않습니다. 저장하는 것은 `data/model_settings.json`의 provider/model preference 정도입니다. 모델명을 모르면 `Use provider default for every agent`를 선택하면 됩니다.

이미 Codex에 로그인되어 있으면 JIMMORIA가 자동으로 `codex_sdk` 또는 `codex_cli` provider를 감지하고 다음 실행부터 모델 설정 화면을 건너뜁니다.

SDK provider는 `openai/codex` Python SDK 구조를 따른다. JIMMORIA는 에이전트 LLM 호출마다 ephemeral thread를 만들고, 기본값으로 `Sandbox.read_only`, `ApprovalMode.deny_all`, JIMMORIA 프로젝트 루트 `cwd`를 사용한다. 리서치 판단용 LLM 호출이므로 저장소 수정이나 추가 승인 요청은 기본적으로 막아둔다.

## How LLMs Work

JIMMORIA는 에이전트가 각자 모델을 직접 고르지 않습니다. 모든 호출은 `ModelGateway`를 통과하고, 작업 종류에 따라 fast/reasoning/writing route로 나뉩니다.

```text
Supervisor chat          fast chat route
Source ingestion         fast/default route
Supervisor planning      reasoning route
Narrative/discovery      reasoning route
Social/on-chain/product  reasoning route
Funding/token review     reasoning route
Obsidian curation        reasoning route
Report synthesis         writing route
```

CrewAI의 agent/task 분리, ChatDev의 phase/workflow와 replay, LangGraph Supervisor의 routing/handoff 패턴, Hermes Agent의 toolset/delegation 운영 방식을 JIMMORIA 구조에 맞게 흡수했습니다. Supervisor는 `supervisor_office` 툴로 task를 만들고 배정하며, 각 전문 에이전트는 먼저 ToolGateway와 SharedMemory로 근거를 모은 뒤 `llm_analysis_pass`로 요약, 근거 부족, 리스크, 다음 액션을 판단합니다.

Full research room 흐름은 다음입니다.

```text
Supervisor plan -> task delegation -> specialist execution
-> Agent Council consensus -> report writing
-> Supervisor final review -> user delivery + Obsidian sync
```

이 LLM pass는 근거를 만들어내는 역할이 아닙니다. 외부 connector가 비어 있으면 “미설정/근거 부족”이라고 표시하고, 실패해도 Research Room 전체가 죽지 않게 fallback summary를 남깁니다. finding confidence는 tool/memory evidence 기준으로 유지하고, 모델의 자신감은 `llm_analysis.confidence`에 따로 저장합니다. 호출 기록은 `data/runs/<room_id>/llm_call_log.json`에 저장됩니다.

지원 모델은 Codex 모델로 고정했습니다.

```text
gpt-5.5              reasoning / writing 기본값
gpt-5.4              강한 agentic workflow 대안
gpt-5.4-mini         supervisor chat / fast extraction 기본값
gpt-5.3-codex        coding-specialized fallback
gpt-5.3-codex-spark  Pro research preview / near-instant iteration
```

OpenAI API Key나 임의 OAuth bearer token provider는 JIMMORIA 모델 설정 화면에서 더 이상 지원하지 않습니다. live LLM은 `codex_sdk` 또는 `codex_cli`로만 붙이고, `offline_fallback`은 테스트/진단용입니다.

## Tool Status

현재 `jimmoria doctor` 기준으로 실제 등록된 read-only connector는 다음입니다.

```text
configured: web_search, fetch_url, parse_html, crawl_website, crawl_docs,
            github_search_repos, read_github_repo,
            coingecko_coin_metadata, dexscreener_search_pairs,
            archive_source_snapshot,
            create_research_room, create_task, assign_task,
            agent_handoff, update_task_status

configured but needs secrets: X/Twitter, Telegram, RootData,
                              Explorer/RPC

configured without secrets: funding/airdrop checker

placeholder/missing connector: Discord, RSS monitor, Dune, The Graph,
                               some advanced market feeds

blocked by design: wallet_sign, swap, transfer, approve,
                   private_key_read, seed_phrase_read
```

즉 브라우저/웹 검색 계열은 바로 동작하고, KOL timeline이나 Telegram/RootData/Explorer 같은 live research connector는 아래 secret을 넣으면 live 호출로 바뀝니다.

```powershell
$env:X_BEARER_TOKEN="..."
$env:TELEGRAM_BOT_TOKEN="..."
$env:TELEGRAM_CHAT_ID="..."      # 선택 사항. bot이 볼 수 있는 채널/그룹만 읽음
$env:ROOTDATA_API_KEY="..."
$env:ETHERSCAN_API_KEY="..."
$env:ETH_RPC_URL="..."           # RPC read-only call을 쓸 때만 필요
```

Telegram Bot API는 임의의 공개 채널 과거 메시지를 스크래핑하는 API가 아니라 bot이 접근 가능한 업데이트를 읽는 방식입니다. 그래서 bot을 채널/그룹에 넣거나 승인된 chat id를 제공해야 합니다.

## Chat Commands

```text
/models                  모델/provider 설정 변경
/company                 에이전트 목록 보기
/doctor                  현재 연결 가능한 기능 확인
/rooms                   Show multi-room workload board
/runs                    이전 실행 목록
/status [room_id]        특정 Research Room 상태
/messages [room_id]      에이전트 협업 메시지
/events [room_id]        UI/replay 이벤트
/report [room_id]        저장된 보고서 출력
/last                    최근 실행 요약
/quit                    종료
```

일반 문장을 입력하면 Research Room이 열립니다.

```text
+------------------------------------------------------------+
| Type a request, URL, /command, or @path/to/file             |
| > AI wallet automation 관련 초기 프로젝트 찾아줘
+------------------------------------------------------------+
```

Natural language report retrieval also works. The Supervisor should not open a new Research Room for this shape of request.

```text
You > 3jane 보고서 만든거 보내봐 전체
Supervisor > finds and prints the saved report

You > 3jane 보고서 만들어봐
Supervisor > treats this as an existing-report lookup unless the message says 리서치/조사/분석/새로
```

## Project Structure

더 자세한 구조와 런타임 설명은 [docs/jimmoria-project-structure.md](docs/jimmoria-project-structure.md)에 정리되어 있습니다.

```text
jimmoria/
  crypto_research_agents/
    cli.py                 jimmoria 명령어 진입점
    console.py             터미널 화면, 히어로, 채팅 UI
    runtime.py             Research Room 실행 흐름
    web.py                 local Web Research HQ dashboard
    agents/                실제 에이전트 구현
    connectors/            Supervisor Office, Web Search, URL, Docs, GitHub, DEX, CoinGecko connector
    core/                  Bus, Memory, Room, ModelGateway, ToolGateway
    storage/               JSON 저장소, run snapshot, Obsidian writer

  config/
    agents/                에이전트 persona, 권한, tool policy
    processes/             Research Room process/task manifest
    models/                모델 라우팅 기본 설정
    skills/                리서치 workflow 정의
    tools/                 tool registry

  templates/
    obsidian/              Source, Project, Report 노트 템플릿

  docs/                    아키텍처와 실행 스펙
  tests/                   smoke test

  data/                    memory, runs, model_settings 출력
  reports/                 markdown 리서치 보고서 출력
  vault/                   Obsidian-style note 출력
```

`data/`, `reports/`, `vault/`는 실행 중 생성되는 로컬 출력 폴더이며 Git에는 올리지 않습니다.

Default CLI paths resolve to the JIMMORIA project root. Running `jimmoria` from `C:\Users\...` still uses `C:\jimmoria\config`, `C:\jimmoria\data`, `C:\jimmoria\reports`, and `C:\jimmoria\vault` unless explicit paths are provided.

## Current MVP

동작 중:

```text
- CLI 채팅 인터페이스
- 보라/핑크 테마의 Rich 기반 실행 로그
- Local Web Research HQ dashboard (`jimmoria web`)
- Research Room orchestration
- Supervisor + controlled P2P Agent Bus
- AgentSpec/persona YAML 로딩
- ProcessSpec 기반 Research Room task manifest
- Agent-level LLM analysis pass
- Codex SDK provider, Codex CLI provider, offline diagnostic fallback
- Web Search/URL/Website/Docs/GitHub/DEX Screener/CoinGecko 기본 connector
- Source content hash, canonical URL, source snapshot, dedupe
- Markdown report 생성
- run snapshot, event log, tool audit log, LLM call log
- Obsidian-style note 생성
```

아직 live 연결 전:

```text
- X/Twitter/KOL 검색
- Telegram/Discord 채널 읽기
- Explorer/RPC contract metadata
- RootData/funding/airdrop/points checker
- DDGS public web search 품질과 검색 provider 가용성
- GitHub commits/releases/activity 심화 분석
- Identity resolver, ticker collision, evidence validator
```

상태 확인:

```powershell
jimmoria doctor
```

## Important Files

```text
config/agents/*.yaml                         에이전트 정의
config/processes/*.yaml                      Research Room process/task manifest
config/tools/tool_registry.yaml              필요한 외부 tool 목록
config/models/model_router.yaml              모델 라우팅 기준
docs/crypto-research-company-v1.4-execution-spec.md
data/runs/<room_id>/events.json              나중에 시각화 UI에서 쓸 replay event stream
```

## Process Manifests

ChatDev의 configurable workflow/phase/role 개념과 crewAI의 `agents.yaml` + `tasks.yaml` + sequential process 패턴을 JIMMORIA 구조에 맞게 흡수했다. 에이전트 내부 구현은 그대로 두고, Research Room의 목표, task 순서, expected output, artifact contract를 `config/processes/`에 분리한다.

```text
config/processes/project_research_room.yaml
config/processes/source_ingestion_room.yaml
crypto_research_agents/core/process_spec.py
```

Runtime은 이 manifest를 읽어 room goals와 agent order를 결정하고, `events.json`의 `room_created.process`에 process metadata를 저장한다.

## Chat Intake Rule

JIMMORIA는 이제 모든 일반 입력을 바로 보고서로 만들지 않는다.

- `pearl 프로젝트 리서치 보고서 만들어봐`처럼 리서치/분석/보고서 요청이 명확하면 Research Room을 연다.
- `3jane 보고서 만들어봐`처럼 특정 기존 산출물을 부르는 말은 먼저 저장된 보고서 조회로 처리한다. 새 리서치가 필요하면 `새로`, `리서치`, `조사`, `분석`을 명시한다.
- `보고서는 한글로 만들어봐`, `로그 스타일 바꿔`, `슈퍼바이저 역할을 사장처럼 가져가` 같은 말은 회사 운영 설정으로 반영한다.
- 설정은 `data/company_settings.json`에 저장된다.
- `/settings`로 현재 회사 설정을 확인할 수 있다.

Supervisor는 단순 진행자가 아니라 회사 사장/총괄 PM이자 오케스트레이터처럼 동작한다. 사용자는 JIMMORIA에 외주를 주는 클라이언트이고, Supervisor가 먼저 의도를 분류한 뒤 Research Room을 열지, 설정을 바꿀지 결정한다. Research Room이 열리면 Supervisor가 목표를 쪼개고, 하위 에이전트에게 일을 하달하고, Agent Council을 조율하고, 마지막 전달 모드를 승인한다.

Important new file:

```text
data/company_settings.json   company operating settings
```

## Supervisor Intake

JIMMORIA의 일반 채팅 상대는 Supervisor다. 사용자는 먼저 Supervisor와 대화하고, Supervisor가 회사 사장/총괄 PM처럼 요청의 종류를 판단해 출력 방식과 내부 작업 여부를 고른다. 화면에는 기계적인 분류 카드 대신 Supervisor의 대화 응답이 먼저 나온다.

```text
리서치/분석/보고서 요청        -> Research Room + agent workflow + dossier
설정/운영/UX/역할 변경        -> company_settings.json 업데이트
확인 질문/운영 대화           -> Supervisor reply
인사/잡담/애매한 입력          -> Supervisor reply, 방향 확인
상태/설정 확인                -> settings/status panel 출력
소스만 저장                   -> 작은 ingestion room + Obsidian Source Note
```

예를 들어 `pearl 프로젝트를 분석해봐`는 리서치 방을 열지만, `보고서는 한글로 만들어`, `슈퍼바이저 권한을 더 크게 가져가`, `로그 스타일을 바꿔` 같은 말은 보고서를 만들지 않고 회사 운영 설정으로 반영된다. `지금 보고서 작성은 한글 위주로 세팅된 게 맞지?` 같은 말은 Research Room 없이 Supervisor가 직접 답한다. `안녕` 같은 인사는 설정으로 저장하지 않는다.

기존 산출물 요청은 더 보수적으로 처리한다. `3jane 보고서 들고와봐`, `3jane 보고서 보내봐`, `3jane 보고서 만들어봐`는 새 Research Room을 열지 않고 `data/runs/*/room.json`과 `reports/*.md`에서 저장된 보고서를 먼저 찾는다. `3jane 새로 리서치 보고서 만들어봐`처럼 새 조사 의도가 분명할 때만 에이전트 방을 연다.

Research Room이 필요한 경우에만 Supervisor가 방을 열고 에이전트들에게 작업을 배정한다. 그렇지 않으면 Supervisor가 바로 답하거나 설정을 반영하고 끝낸다. 방이 열리면 `orchestration_plan` 이벤트와 finding이 저장되어 plan -> delegate -> coordinate -> council -> final review 흐름을 나중에 CLI/Web UI에서 추적할 수 있다.

Supervisor 대화는 `supervisor_chat` 모델 라우트를 사용한다. live LLM이 설정되어 있으면 일반 챗봇처럼 자연어로 답하고, 모델이 없으면 로컬 fallback이 짧게 응답한다.

CLI 입력창은 제출 후 같은 내용을 큰 `You` 패널로 다시 반복하지 않는다. 입력 박스는 다음 입력용으로 다시 그려지고, 사용자가 보낸 문장은 위쪽 대화 로그에 `You > ...` 형태로 올라간다. 그 다음 Supervisor가 현재 처리 중인 일을 짧게 보여주고 답변하거나 Research Room을 연다.

최근 CLI UX는 Mato, Conduit, Spettro, MetaGPT, ChatDev, ZeroHuman 같은 멀티에이전트/zero-human-company 계열 프로젝트의 terminal workspace와 visible orchestration 패턴을 참고했다. 적용 내용은 [docs/jimmoria-cli-ui-reference-notes.md](docs/jimmoria-cli-ui-reference-notes.md)에 정리되어 있다.

Research Room runtime 로그는 기본적으로 큰 카드가 아니라 compact stream으로 올라간다. 실행 중에도 하단의 `JIMMORIA HQ` dock은 유지된다. 새 이벤트가 올라오면 이전 dock을 지우고 이벤트를 찍은 뒤 다시 dock을 그려서, 화면 아래에는 항상 회사 채팅창이 남아있는 것처럼 보인다.

```text
Room > OPEN room_abc123 | agents 10 | pearl 프로젝트 리서치
Plan > ORCHESTRATE 10 tasks | checkpoints 5 | Supervisor set the orchestration plan
Agent > RUN ingestion_agent | Extracting source metadata
Tool > RUN discovery_agent -> web_search | pearl crypto project
Output > Report written | reports/pearl-room_abc123.md
```

예전처럼 큰 카드/보드 중심으로 보고 싶으면 `JIMMORIA_EVENT_STYLE=cards`를 설정한다. 현재 board만 보고 싶으면 채팅 중 `/board`를 입력한다.

## Multi-Room Workload Board

여러 작업을 동시에 굴리거나 이전 room들을 비교할 때는 workload board를 쓴다. 지금 단계에서는 실제 백그라운드 병렬 실행 큐가 아니라, 저장된 Research Room들을 한 화면에서 운영 보드처럼 보는 기능이다.

```powershell
jimmoria rooms
```

채팅 중에는 다음처럼 입력한다.

```text
/rooms
/work
/workboard
```

보드는 최근 Research Room들을 `state`, `progress`, `quality`, `latest work`, `report` 단위로 요약한다. 자세한 로그는 여전히 `/events <room_id>`, `/messages <room_id>`, `data/runs/<room_id>`에서 확인한다.

## Research Quality Gate

JIMMORIA는 이제 Research Room이 끝났다고 해서 무조건 완료 보고서라고 부르지 않습니다.

- `research_complete`: source-backed 후보와 evidence URL이 있는 경우
- `insufficient_evidence`: 후보가 전부 `mvp_placeholder`이거나 evidence URL이 0개인 경우

`insufficient_evidence`가 나오면 파일은 저장되지만, 제목이 `리서치 미완료 / Research Not Completed`로 표시됩니다. 이 경우는 실제 리서치 완료본이 아니라 "근거가 부족해서 아직 보고서로 확정할 수 없음"이라는 진단 메모입니다.

## Hermes-Inspired Operating Layer

JIMMORIA now has a thin operating layer for toolsets, scheduled jobs, worker profiles, playbooks, session search, and safety boundaries. It is not a generic assistant layer; it stays focused on read-only Web3 research and report artifacts.

```powershell
jimmoria tools list
jimmoria tools list --toolset research_basic
jimmoria cron list
jimmoria cron status
jimmoria cron run early_radar_30m
jimmoria cron create my_job --schedule "every 2h" --workflow early_radar_v1
jimmoria profile list
jimmoria playbook list
jimmoria sessions search "0x..."
```

New operating files:

```text
config/toolsets.yaml              tool registry, toolsets, read-only boundary
config/jobs.yaml                  scheduled research job specs
config/profiles.yaml              worker profiles and allowed toolsets
research_playbooks/*.md           reusable research playbooks
crypto_research_agents/tools/     tool registry loader
crypto_research_agents/core/      scheduler, profile, playbook modules
crypto_research_agents/storage/   richer artifact archive and session search
```

Workflow archives now include:

```text
data/runs/<room_id>/
  input.json
  tool_calls.jsonl
  sources.json
  findings.json
  candidates.json
  report.md
  report.telegram.md
  report.json
```

Safety boundary:

```text
- allowed by default: read_only, artifact_write
- blocked: wallet signing, swap, transfer, approve, private key, seed phrase
- report guard: no investment-advice language
- evidence guard: factual claims need a URL or an explicit unverified/insufficient-evidence label
```

## Workflow Commands

ChatDev식 YAML workflow layer가 추가되었습니다. 기존 Research Room을 대체하지 않고, 회사 업무 흐름과 replay artifact를 남기는 레이어입니다.

```powershell
jimmoria workflow list
jimmoria workflow show early_radar_v1
jimmoria workflow run project_diligence_v1 --text "pearl crypto project" --json
jimmoria workflow events <room_id> --tail
jimmoria research --workflow early_radar_v1 --text "new PoW projects" --json
```

Workflow artifact는 `data/runs/<room_id>/workflow_trace.json`, `events.jsonl`, `sources.json`, `findings.json`, `candidates.json`, `report.json`에 저장됩니다.
