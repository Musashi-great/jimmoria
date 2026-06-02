# JIMMORIA

JIMMORIA는 크립토 리서치 전용 멀티에이전트 회사 CLI입니다.

사용자는 터미널에서 채팅을 치고, Supervisor가 Research Room을 열어 여러 에이전트에게 일을 나눕니다. 에이전트들은 소스 정리, 내러티브 분석, 후보 프로젝트 발굴, KOL/소셜 체크, 온체인/제품/토큰 체크, 보고서 작성, Obsidian 노트 정리를 담당합니다.

현재는 MVP입니다. 에이전트 협업 구조와 보고서 생성 흐름은 동작하고, Web Search/URL/Website/Docs/GitHub/DEX Screener/CoinGecko 기본 connector가 ToolGateway 뒤에 붙어 있습니다. X/Twitter, Telegram, Discord, RootData, Explorer/RPC, funding/airdrop 커넥터는 아직 placeholder 상태입니다.

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

데모 실행:

```powershell
jimmoria demo
```

## Model Setup

처음 실행하면 모델 선택 화면이 나옵니다.

```text
1. Codex OAuth / ChatGPT login code
2. OpenAI API Key
3. Offline fallback
```

추천 흐름은 `Codex OAuth / ChatGPT login code`입니다. Codex CLI의 `codex login --device-auth` 방식으로 ChatGPT 로그인 코드를 입력합니다.

한 번 로그인하면 보통 다시 로그인할 필요가 없습니다. 로그아웃하거나, 다른 컴퓨터로 옮기거나, 세션이 만료된 경우에만 다시 로그인하면 됩니다.

JIMMORIA는 토큰을 저장하지 않습니다. 저장하는 것은 `data/model_settings.json`의 provider/model preference 정도입니다. 모델명을 모르면 `Use provider default for every agent`를 선택하면 됩니다.

이미 Codex CLI에 로그인되어 있으면 JIMMORIA가 자동으로 `codex_cli` provider를 감지하고 다음 실행부터 모델 설정 화면을 건너뜁니다.

## Chat Commands

```text
/models                  모델/provider 설정 변경
/company                 에이전트 목록 보기
/doctor                  현재 연결 가능한 기능 확인
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

## Project Structure

더 자세한 구조와 런타임 설명은 [docs/jimmoria-project-structure.md](docs/jimmoria-project-structure.md)에 정리되어 있습니다.

```text
jimmoria/
  crypto_research_agents/
    cli.py                 jimmoria 명령어 진입점
    console.py             터미널 화면, 히어로, 채팅 UI
    runtime.py             Research Room 실행 흐름
    agents/                실제 에이전트 구현
    connectors/            Web Search, URL, Docs, GitHub, DEX, CoinGecko connector
    core/                  Bus, Memory, Room, ModelGateway, ToolGateway
    storage/               JSON 저장소, run snapshot, Obsidian writer

  config/
    agents/                에이전트 persona, 권한, tool policy
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

## Current MVP

동작 중:

```text
- CLI 채팅 인터페이스
- 보라/핑크 테마의 Rich 기반 실행 로그
- Research Room orchestration
- Supervisor + controlled P2P Agent Bus
- AgentSpec/persona YAML 로딩
- Codex CLI login, OpenAI API key, offline fallback provider
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
config/tools/tool_registry.yaml              필요한 외부 tool 목록
config/models/model_router.yaml              모델 라우팅 기준
docs/crypto-research-company-v1.4-execution-spec.md
data/runs/<room_id>/events.json              나중에 시각화 UI에서 쓸 replay event stream
```
## Chat Intake Rule

JIMMORIA는 이제 모든 일반 입력을 바로 보고서로 만들지 않는다.

- `pearl 프로젝트 리서치 보고서 만들어봐`처럼 리서치/분석/보고서 요청이 명확하면 Research Room을 연다.
- `보고서는 한글로 만들어봐`, `로그 스타일 바꿔`, `슈퍼바이저 역할을 사장처럼 가져가` 같은 말은 회사 운영 설정으로 반영한다.
- 설정은 `data/company_settings.json`에 저장된다.
- `/settings`로 현재 회사 설정을 확인할 수 있다.

Supervisor는 단순 진행자가 아니라 회사 사장/총괄 PM처럼 동작한다. 사용자는 JIMMORIA에 외주를 주는 클라이언트이고, Supervisor가 먼저 의도를 분류한 뒤 Research Room을 열지, 설정을 바꿀지 결정한다.

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

Research Room이 필요한 경우에만 Supervisor가 방을 열고 에이전트들에게 작업을 배정한다. 그렇지 않으면 Supervisor가 바로 답하거나 설정을 반영하고 끝낸다.

Supervisor 대화는 `supervisor_chat` 모델 라우트를 사용한다. live LLM이 설정되어 있으면 일반 챗봇처럼 자연어로 답하고, 모델이 없으면 로컬 fallback이 짧게 응답한다.

CLI 입력창은 제출 후 같은 내용을 큰 `You` 패널로 다시 반복하지 않는다. 입력 박스는 다음 입력용으로 다시 그려지고, 사용자가 보낸 문장은 위쪽 대화 로그에 `You > ...` 형태로 올라간다. 그 다음 Supervisor가 현재 처리 중인 일을 짧게 보여주고 답변하거나 Research Room을 연다.

최근 CLI UX는 Mato, Conduit, Spettro, MetaGPT, ChatDev, ZeroHuman 같은 멀티에이전트/zero-human-company 계열 프로젝트의 terminal workspace와 visible orchestration 패턴을 참고했다. 적용 내용은 [docs/jimmoria-cli-ui-reference-notes.md](docs/jimmoria-cli-ui-reference-notes.md)에 정리되어 있다.
