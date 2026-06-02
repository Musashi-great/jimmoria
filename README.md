# JIMMORIA

JIMMORIA는 크립토 리서치 전용 멀티에이전트 회사 CLI입니다.

사용자는 터미널에서 채팅을 치고, Supervisor가 Research Room을 열어 여러 에이전트에게 일을 나눕니다. 에이전트들은 소스 정리, 내러티브 분석, 후보 프로젝트 발굴, KOL/소셜 체크, 온체인/제품/토큰 체크, 보고서 작성, Obsidian 노트 정리를 담당합니다.

현재는 MVP입니다. 에이전트 협업 구조와 보고서 생성 흐름은 동작하고, X/Twitter, Telegram, GitHub, Explorer 같은 실시간 외부 리서치 커넥터는 아직 placeholder 상태입니다.

## Quick Start

처음 한 번만 설치합니다.

```powershell
cd C:\jimmoria
python -m pip install -e .
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
jimmoria> AI wallet automation 관련 초기 프로젝트 찾아줘
```

## Project Structure

```text
jimmoria/
  crypto_research_agents/
    cli.py                 jimmoria 명령어 진입점
    console.py             터미널 화면, 히어로, 채팅 UI
    runtime.py             Research Room 실행 흐름
    agents/                실제 에이전트 구현
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
- Research Room orchestration
- Supervisor + controlled P2P Agent Bus
- AgentSpec/persona YAML 로딩
- Codex CLI login, OpenAI API key, offline fallback provider
- Markdown report 생성
- run snapshot, event log, tool audit log, LLM call log
- Obsidian-style note 생성
```

아직 live 연결 전:

```text
- X/Twitter/KOL 검색
- Telegram/Discord 채널 읽기
- GitHub/docs/website crawler
- Explorer/RPC/DEX token metadata
- RootData/funding/airdrop/points checker
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
