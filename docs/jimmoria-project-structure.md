# JIMMORIA Project Structure

이 문서는 JIMMORIA 프로젝트의 전체 구조, 런타임 개념, 에이전트 역할, 데이터 흐름, 저장 위치, 앞으로 붙일 외부 커넥터의 위치를 설명한다.

JIMMORIA는 크립토 가격 매매 도구가 아니라, 리서치 전용 멀티에이전트 회사 CLI다. 사용자는 터미널에서 자연어로 리서치 요청을 입력하고, Supervisor가 Research Room을 열어 여러 전문 에이전트에게 일을 나눈다. 에이전트들은 소스 정리, 내러티브 분석, 초기 프로젝트 후보 발굴, KOL/소셜 체크, 온체인/제품/토큰 체크, 보고서 작성, Obsidian 노트 정리를 수행한다.

현재 버전은 MVP다. 멀티에이전트 협업 구조, 보고서 생성, 런 저장, Obsidian-style 노트 생성은 동작한다. X/Twitter, Telegram, Discord, RootData, GitHub, Explorer 같은 실시간 외부 리서치 커넥터는 ToolGateway에 연결될 자리와 권한 구조만 잡혀 있고 아직 placeholder 상태다.

## 1. 프로젝트 목표

JIMMORIA의 핵심 목표는 "채팅으로 조종하는 크립토 리서치 회사"를 CLI에서 구현하는 것이다.

사용자가 원하는 주요 작업은 다음과 같다.

- 아티클, 트윗, 링크, PDF, 문서 같은 소스를 넣고 리서치 보고서 생성
- KOL 핸들 목록 수집과 KOL별 언급 프로젝트 추적
- X/Twitter, Telegram, Discord, RSS 등에서 내러티브와 프로젝트 신호 탐지
- 초기에 주목받기 전의 프로젝트 후보 발굴
- Docs, GitHub, 웹사이트, 토큰 상태, 포인트/에어드랍 단서 확인
- 결과를 Markdown 보고서와 Obsidian Vault 형태로 정리
- 나중에 UI에서 에이전트 작업 흐름을 시각적으로 replay할 수 있도록 이벤트 로그 저장

## 2. 최상위 폴더 구조

```text
jimmoria/
  crypto_research_agents/
    cli.py
    console.py
    runtime.py
    agents/
    core/
    storage/

  config/
    agents/
    models/
    skills/
    tools/

  templates/
    obsidian/

  docs/
  tests/

  data/
  reports/
  vault/
```

핵심은 `crypto_research_agents/`가 실제 실행 코드이고, `config/`가 회사의 에이전트 정의와 도구/모델 정책을 담는다는 점이다. `data/`, `reports/`, `vault/`는 실행하면서 생성되는 로컬 출력 폴더라 Git에는 올리지 않는다.

## 3. 실행 진입점

설치 후 실행 명령은 다음이다.

```powershell
jimmoria
```

이 명령은 `pyproject.toml`의 script entrypoint로 연결된다.

```toml
[project.scripts]
jimmoria = "crypto_research_agents.cli:main"
crypto-research = "crypto_research_agents.cli:main"
```

즉, `jimmoria`를 치면 `crypto_research_agents/cli.py`의 `main()`이 실행된다.

주요 CLI 명령은 다음과 같다.

```text
jimmoria                    대화형 Research HQ 실행
jimmoria hq                 대화형 Research HQ 실행
jimmoria chat               hq와 같은 채팅형 콘솔
jimmoria demo               내장 데모 리서치 실행
jimmoria research           텍스트, 파일, URL 기반 리서치 실행
jimmoria article            research 별칭
jimmoria add-source         소스만 저장하고 Obsidian Source Note 생성
jimmoria doctor             현재 기능 연결 상태 확인
jimmoria runs               이전 실행 목록
jimmoria status <room_id>   특정 Research Room 상태
jimmoria messages <room_id> 에이전트 협업 메시지
jimmoria events <room_id>   UI/replay 이벤트
jimmoria show-report <id>   저장된 보고서 출력
```

## 4. 전체 아키텍처

JIMMORIA는 controlled P2P 구조다. 모든 에이전트가 완전 자유롭게 서로 호출하는 것이 아니라, Supervisor와 Agent Bus, Shared Memory, Tool Gateway를 중심으로 협업한다.

```mermaid
flowchart LR
    User[User CLI Chat] --> CLI[cli.py]
    CLI --> Console[console.py]
    CLI --> Runtime[ResearchRuntime]

    Runtime --> Room[ResearchRoom]
    Runtime --> Bus[CollaborationBus]
    Runtime --> Memory[SharedMemory]
    Runtime --> Model[ModelGateway]
    Runtime --> Tools[ToolGateway]
    Runtime --> Events[Event Log]

    Model --> Provider[Codex CLI / Codex OAuth / OpenAI / Offline]
    Tools --> External[External Connectors Placeholder]

    Runtime --> Agents[Agents]
    Agents --> Bus
    Agents --> Memory
    Agents --> Model
    Agents --> Tools

    Runtime --> Runs[data/runs]
    Runtime --> Reports[reports]
    Runtime --> Vault[vault]
```

중앙 개념은 다음과 같다.

| 개념 | 파일 | 역할 |
|---|---|---|
| CLI | `crypto_research_agents/cli.py` | 명령어 파싱, 채팅 루프, 모델 설정 패널 |
| Console | `crypto_research_agents/console.py` | JIMMORIA 로고, 박스형 입력창, 진행 상황 출력 |
| Runtime | `crypto_research_agents/runtime.py` | Research Room 생성과 에이전트 실행 순서 관리 |
| ResearchRoom | `core/room.py` | 하나의 리서치 작업 단위와 상태 |
| CollaborationBus | `core/bus.py` | 에이전트 요청, 응답, 핸드오프, 업데이트 로그 |
| SharedMemory | `core/memory.py` | 소스, 프로젝트 후보, finding, entity graph 저장 |
| ModelGateway | `core/model_gateway.py` | task type에 따라 모델 라우팅 |
| LLM Provider | `core/llm_provider.py` | Codex CLI, Codex OAuth, OpenAI, offline fallback 연결 |
| ToolGateway | `core/tool_gateway.py` | 에이전트별 tool 권한 검사와 audit log |
| Storage | `storage/` | memory, run snapshot, Obsidian note 저장 |

## 5. Research Room 실행 흐름

일반 리서치 요청은 `ResearchRuntime.run_article_research()`로 들어간다.

실행 순서는 현재 다음과 같다.

```text
1. ResearchRoom 생성
2. room_created 이벤트 기록
3. supervisor_agent 실행
4. ingestion_agent 실행
5. narrative_agent 실행
6. discovery_agent 실행
7. social_kol_agent 실행
8. contract_onchain_agent 실행
9. product_tech_agent 실행
10. funding_token_agent 실행
11. report_agent 실행
12. obsidian_curator_agent 실행
13. room_completed 이벤트 기록
14. memory.json, run snapshot, report, vault note 저장
```

상태는 `RuntimeState` 값으로 이동한다.

```text
created
assigned
running
waiting_for_tool
ready_for_report
writing_report
obsidian_syncing
completed
```

현재는 순차 실행에 가깝지만, 구조상 각 에이전트의 요청/응답은 `CollaborationBus`에 기록된다. 나중에 병렬 실행, 비동기 큐, UI replay를 붙일 때 이 bus와 event log가 기반이 된다.

## 6. 에이전트 구성

현재 실제 런타임에서 기본 실행되는 에이전트는 `runtime.py`의 `DEFAULT_AGENTS`에 정의된 10개다.

| Agent ID | 구현 클래스 | 역할 | 현재 동작 |
|---|---|---|---|
| `supervisor_agent` | `SupervisorAgent` | 목표와 실행 방향 설정 | Research Room 목표, 참여 에이전트, 모델 선택 정보를 finding으로 기록 |
| `ingestion_agent` | `IngestionAgent` | 소스 저장과 메타데이터 추출 | 입력 소스를 `SharedMemory.sources`에 저장하고 summary/entities/keywords 추출 |
| `narrative_agent` | `NarrativeAgent` | 내러티브 분류 | AI wallet, Consumer Crypto, DeFi Automation 등 taxonomy 기반 narrative 분류 |
| `discovery_agent` | `DiscoveryAgent` | 초기 프로젝트 후보 발굴 | narrative 기반 MVP 후보 프로젝트를 생성하고 검증 에이전트에게 요청 |
| `social_kol_agent` | `SocialKOLAgent` | KOL/소셜 신호 확인 | `x_search_posts` tool을 호출하지만 현재 connector 미연결이라 placeholder finding 생성 |
| `contract_onchain_agent` | `ContractOnchainAgent` | 체인, 토큰, 컨트랙트 확인 | `get_contract_address` tool을 호출하지만 현재 Explorer/RPC 미연결 |
| `product_tech_agent` | `ProductTechAgent` | Docs, GitHub, 제품 상태 확인 | `crawl_docs` tool을 호출하지만 현재 crawler 미연결 |
| `funding_token_agent` | `FundingTokenAgent` | 투자자, 포인트, 토큰 기회 확인 | `check_airdrop_points` tool을 호출하지만 현재 funding/token connector 미연결 |
| `report_agent` | `ReportAgent` | 보고서 작성 | findings와 candidates를 Markdown dossier로 합성 |
| `obsidian_curator_agent` | `ObsidianCuratorAgent` | Obsidian-style 노트 정리 | Source, Project, Narrative, Report 노트 작성 |

`config/agents/`에는 기본 실행되지 않는 추가 설계용 agent spec도 있다.

| Agent Spec | 상태 | 목적 |
|---|---|---|
| `monitor_24h_agent.yaml` | planned | 24시간 RSS, X, Telegram, GitHub, docs 변화 감지 |
| `memory_retrieval_agent.yaml` | planned | 과거 실행, entity graph, vector memory 검색 |
| `tool_policy_agent.yaml` | planned | tool 권한, secret scan, safety policy 관리 |

이 agent spec들은 아직 `DEFAULT_AGENTS`에 들어가 있지 않지만, 회사 구조 확장 시 추가될 수 있다.

## 7. AgentSpec와 persona YAML

`config/agents/*.yaml`은 각 에이전트의 persona, 역할, 금지사항, tool allowlist, memory 접근 범위, output schema를 정의한다.

이 파일들은 `core/agent_spec.py`의 `AgentSpecRegistry.load_dir()`로 로드된다.

AgentSpec이 담는 주요 필드는 다음이다.

```text
agent_id
name
persona_name
identity
personality
mission
scope
model_policy
memory_scope
tools.allow
tools.deny
hooks
output_schema
collaboration
must_follow
must_not
```

에이전트는 실행 중 `self.system_prompt()`를 통해 이 YAML 내용을 LLM system prompt로 변환한다. 그래서 코드는 에이전트의 행동 골격을 담당하고, YAML은 그 에이전트가 어떤 회사원처럼 행동해야 하는지를 정의한다.

## 8. Collaboration Bus

`core/bus.py`의 `CollaborationBus`는 append-only 협업 기록이다.

메시지 타입은 크게 다음과 같다.

```text
REQUEST   한 에이전트가 다른 에이전트에게 작업 요청
RESPONSE  요청에 대한 결과 반환
HANDOFF   다음 에이전트에게 컨텍스트 넘김
UPDATE    전체 혹은 특정 대상에게 상태 공유
```

예를 들어 `discovery_agent`는 후보 프로젝트를 만든 뒤 다음 에이전트들에게 요청을 보낸다.

```text
discovery_agent -> social_kol_agent
discovery_agent -> contract_onchain_agent
discovery_agent -> product_tech_agent
discovery_agent -> funding_token_agent
```

이 기록은 실행 후 `data/runs/<room_id>/messages.json`에 저장된다. 나중에 CLI나 웹 UI에서 "누가 누구에게 무엇을 요청했는지" 보여줄 때 핵심 데이터가 된다.

## 9. Shared Memory

`core/memory.py`의 `SharedMemory`는 회사의 공통 기억이다.

현재 저장하는 데이터는 다음이다.

| 데이터 | 클래스 | 설명 |
|---|---|---|
| Source | `SourceRecord` | 사용자가 넣은 아티클, 링크, 텍스트, 원문과 메타데이터 |
| Project | `ProjectCandidate` | discovery 단계에서 만들어진 후보 프로젝트 |
| Finding | `FindingRecord` | 각 에이전트가 남긴 조사 결과 |
| Entity Graph | `dict[str, set[str]]` | 프로젝트와 narrative 등 엔티티 관계 |

실행 중에는 메모리 객체로 존재하고, 실행 후 `storage/json_store.py`를 통해 `data/memory.json`에 저장된다.

현재는 간단한 JSON 메모리다. 나중에 붙일 수 있는 확장 방향은 다음이다.

- SQLite/Postgres 기반 project DB
- vector DB 기반 source retrieval
- KOL profile DB
- entity graph persistence
- run 간 중복 프로젝트 dedupe

## 10. Model Gateway와 LLM Provider

모델 호출은 에이전트가 직접 모델을 고르는 방식이 아니라 `ModelGateway`를 통한다.

`core/model_gateway.py`는 `task_type`에 따라 모델 route를 선택한다.

| Task Type | Route |
|---|---|
| `source_ingestion` | fast/default model |
| `narrative_reasoning` | reasoning model |
| `supervision` | reasoning model |
| `report_writing` | writing model |
| `final_synthesis` | writing model |
| `embedding_search` | embedding model |

실제 provider는 `core/llm_provider.py`에서 결정된다.

| Provider | 조건 | 설명 |
|---|---|---|
| `codex_cli` | `LLM_PROVIDER=codex_cli` | 로컬 Codex CLI 로그인 세션으로 `codex exec` 호출 |
| `codex_oauth` | `CODEX_OAUTH_TOKEN` 등 명시적 token source | OpenAI-compatible endpoint에 bearer token으로 요청 |
| `openai` | `OPENAI_API_KEY` | OpenAI Python SDK 사용 |
| `offline_fallback` | 설정 없음 | deterministic local fallback, 테스트와 MVP 안전장치 |

CLI에서 `/models`를 실행하면 모델/provider 설정 화면이 나온다. 설정은 `data/model_settings.json`에 저장된다. 토큰 자체는 저장하지 않고 provider/model preference만 저장한다.

## 11. Tool Gateway와 외부 커넥터

`core/tool_gateway.py`의 `ToolGateway`는 에이전트가 외부 도구를 직접 만지지 않도록 막는 중간 계층이다.

역할은 세 가지다.

```text
1. 에이전트별 tool 권한 검사
2. 실제 tool connector 호출
3. tool audit log 기록
```

현재 MVP에서는 많은 외부 tool이 등록되어 있지 않다. 그래서 에이전트가 tool을 호출하면 다음 같은 결과가 저장된다.

```json
{
  "status": "unconfigured",
  "tool": "x_search_posts",
  "message": "Tool connector is not configured in MVP runtime.",
  "data": null
}
```

이 로그는 `data/runs/<room_id>/tool_audit_log.json`에 저장된다.

필요한 tool 목록과 우선순위는 `config/tools/tool_registry.yaml`에 정리되어 있다.

중요한 live stack은 다음이다.

```text
x_search_posts
x_get_user_timeline
x_build_kol_list
rss_monitor_feed
crawl_website
crawl_docs
github_search_repos
read_github_repo
coingecko_coin_metadata
dexscreener_search_pairs
explorer_lookup
rootdata_search_projects
source_cache_write
vector_search
vector_upsert
render_markdown
write_note
```

## 12. Storage와 출력 파일

JIMMORIA는 실행 결과를 여러 형태로 저장한다.

| 경로 | 설명 |
|---|---|
| `data/memory.json` | 전체 SharedMemory 저장 |
| `data/model_settings.json` | provider/model preference 저장 |
| `data/runs/<room_id>/room.json` | ResearchRoom 상태 저장 |
| `data/runs/<room_id>/messages.json` | CollaborationBus 메시지 저장 |
| `data/runs/<room_id>/tool_audit_log.json` | ToolGateway 호출 기록 |
| `data/runs/<room_id>/llm_call_log.json` | ModelGateway 호출 기록 |
| `data/runs/<room_id>/events.json` | CLI/UI replay용 이벤트 로그 |
| `reports/*.md` | ReportAgent가 작성한 Markdown 보고서 |
| `vault/10_Projects/*.md` | Obsidian project note |
| `vault/20_Sources/*.md` | Obsidian source note |
| `vault/30_Narratives/*.md` | Obsidian narrative note |
| `vault/50_Reports/*.md` | Obsidian report note |

특히 `events.json`은 나중에 시각화 UI를 만들 때 중요하다. 현재 CLI에서는 이벤트를 실시간으로 받아 에이전트 진행 상태를 출력하고, 나중에 웹 화면에서는 이 이벤트 스트림을 replay해서 "어떤 에이전트가 언제 시작했고 언제 끝났는지" 보여줄 수 있다.

## 13. Obsidian Vault 구조

`storage/obsidian_store.py`는 local filesystem 기반 Obsidian-style Vault를 만든다.

```text
vault/
  10_Projects/
  20_Sources/
  30_Narratives/
  50_Reports/
```

각 note는 frontmatter를 포함한다.

Project note 예시 필드:

```yaml
type: project
project_id: proj_xxxxx
project_name: Example
chain: unknown
narrative:
  - AI x Wallet Automation
token_status: unknown
early_radar_score: 65
created_at: ...
```

Source note에는 source_id, source_type, title, url, extracted metadata, content가 들어간다. Report note에는 최종 Markdown dossier가 들어간다.

## 14. 현재 동작하는 리서치 시나리오

### Case 1. 사용자가 아티클이나 리서치 질문을 입력

```text
User input
  -> CLI
  -> ResearchRuntime.run_article_research
  -> Ingestion
  -> Narrative
  -> Discovery
  -> Social / Contract / Product / Funding checks
  -> Report
  -> Obsidian sync
```

이 경우 핵심은 다음이다.

```text
아티클/질문 -> 기억화 -> 내러티브 추출 -> 유사 후보 생성 -> 검증 placeholder -> 보고서
```

### Case 2. 24시간 모니터가 신호를 발견

아직 런타임에 연결되지 않았지만 설계상 흐름은 다음이다.

```text
monitor_24h_agent
  -> signal queue
  -> supervisor_agent
  -> discovery_agent
  -> 검증 에이전트들
  -> daily radar report
  -> Obsidian watchlist
```

이를 위해 `monitor_24h_agent.yaml`과 tool registry의 `monitor_*`, `rss_monitor_feed`, `push_signal_queue` 계열 도구가 준비되어 있다.

## 15. 코드 파일별 설명

### `crypto_research_agents/cli.py`

CLI entrypoint다. 명령어 파싱, interactive chat loop, `/models`, `/doctor`, `/runs`, `/report` 같은 명령 처리, 모델 설정 저장을 담당한다.

중요 함수:

```text
main()
chat_command()
handle_chat_command()
configure_model_panel()
configure_codex_oauth()
configure_model_routes()
doctor_command()
```

### `crypto_research_agents/console.py`

터미널 UI를 담당한다. 시작 로고, 보라/핑크 3D 느낌의 JIMMORIA 배너, 닫힌 박스형 입력창, 에이전트 진행 상태 출력, 보고서 preview를 담당한다.

중요 함수:

```text
JimmoriaConsole.print_intro()
JimmoriaConsole.read_chat_input()
JimmoriaConsole.handle_event()
print_jimmoria_logo()
jimmoria_3d_logo_layers()
```

### `crypto_research_agents/runtime.py`

회사 운영 엔진이다. ResearchRoom을 만들고 정해진 순서대로 에이전트를 실행한다. 각 에이전트 시작/완료 이벤트를 만들고, 실행 후 memory와 run snapshot을 저장한다.

중요 함수:

```text
ResearchRuntime.run_article_research()
ResearchRuntime.run_source_ingestion()
ResearchRuntime._run_agent()
ResearchRuntime._emit()
default_policy()
```

### `crypto_research_agents/agents/`

각 전문 에이전트 구현이 들어 있다.

```text
base.py
supervisor.py
ingestion.py
narrative.py
discovery.py
social_kol.py
contract_onchain.py
product_tech.py
funding_token.py
report.py
obsidian_curator.py
```

### `crypto_research_agents/core/`

런타임의 공통 도메인 객체가 들어 있다.

```text
agent_spec.py       YAML persona/spec loader
bus.py              CollaborationBus
message.py          AgentMessage model
memory.py           SharedMemory, SourceRecord, ProjectCandidate, FindingRecord
model_gateway.py    model route selector
llm_provider.py     Codex/OpenAI/offline provider
room.py             ResearchRoom
runtime_state.py    room status enum
tool_gateway.py     tool policy, calls, audit log
tool_call.py        tool call record
hooks.py            before/after hook engine
capabilities.py     doctor command capability check
```

### `crypto_research_agents/storage/`

파일 저장 담당이다.

```text
json_store.py       data/memory.json load/save
run_store.py        data/runs/<room_id> snapshot save/load
obsidian_store.py   vault note writer
paths.py            safe filename helper
```

## 16. Config 파일별 설명

### `config/agents/`

에이전트별 persona와 policy다. 코드에서 각 에이전트의 기능 골격을 정의하고, config에서는 그 에이전트가 어떤 관점과 제약으로 일해야 하는지 정의한다.

### `config/models/model_router.yaml`

모델 route와 환경변수 기준이다. 실제 코드의 `ModelGateway`와 CLI model setup이 이 개념을 따른다.

### `config/tools/tool_registry.yaml`

필요하거나 사용하면 좋은 외부 tool 전체 목록이다. 각 tool의 owner agent, priority, implementation status, required secrets, source docs를 담는다.

### `config/skills/`

리서치 workflow 정의다.

```text
article_ingestion.yaml
project_research.yaml
```

현재는 문서/설계 성격이 강하고, 앞으로 runtime이 skill definition을 더 직접 사용하도록 확장할 수 있다.

## 17. 테스트 구조

테스트는 `tests/test_smoke.py`에 모여 있다.

현재 테스트하는 범위는 다음이다.

```text
- JIMMORIA CLI banner
- pyproject script entrypoint
- article research loop output
- agent specs load
- CLI research/events command
- Codex OAuth/Codex CLI/OpenAI/offline provider selection
- model setup flow
- startup model setup skip
- doctor capability status
- tool registry required stack
- tool audit log
- boxed chat input prompt
- purple/pink 3D logo palette
```

실행:

```powershell
python -m unittest discover -s tests -v
```

## 18. 현재 한계

현재 MVP는 리서치 회사의 뼈대와 협업 흐름을 구현한 단계다.

구체적인 한계는 다음과 같다.

- X/Twitter API가 아직 연결되지 않았다.
- Telegram/Discord reader가 아직 연결되지 않았다.
- GitHub/docs crawler가 아직 실제 connector로 등록되지 않았다.
- Explorer/RPC/DEX Screener/CoinGecko/RootData connector가 아직 등록되지 않았다.
- DiscoveryAgent는 현재 live discovery가 아니라 narrative 기반 placeholder 후보를 만든다.
- Social/Contract/Product/Funding 에이전트는 tool call을 시도하지만 `unconfigured` audit log를 남기는 상태다.
- Vector DB와 entity graph persistence는 아직 간단한 JSON memory 수준이다.

즉, 지금은 "회사 운영 시스템"이 먼저 만들어졌고, 외부 리서치 직원들이 사용할 실제 live 도구를 붙이는 단계가 다음이다.

## 19. 다음 개발 순서 제안

현재 구조 기준으로 다음 순서가 자연스럽다.

```text
1. ToolGateway에 실제 connector registration 구조 추가
2. RSS monitor부터 붙여서 비용 적은 24h signal input 구현
3. GitHub/docs crawler 구현
4. X/KOL search와 KOL profile DB 구현
5. RootData/CoinGecko/DEX Screener/Explorer connector 구현
6. DiscoveryAgent를 placeholder 후보 생성에서 live source 기반 후보 생성으로 변경
7. ReportAgent에 citation/evidence validator 추가
8. events.json 기반 CLI replay 또는 web visualizer 구현
9. SharedMemory를 SQLite/vector DB로 확장
10. monitor_24h_agent를 runtime에 연결해서 Daily Radar 자동화
```

## 20. 한 줄 요약

JIMMORIA는 현재 "채팅형 CLI + Research Room + controlled P2P Agent Bus + Shared Memory + Model Gateway + Tool Gateway + Markdown/Obsidian output"까지 구현된 크립토 리서치 회사 MVP다. 다음 핵심 작업은 외부 live research connectors를 ToolGateway 뒤에 붙이고, Discovery/Social/Product/Funding 에이전트를 실제 데이터 기반으로 업그레이드하는 것이다.
