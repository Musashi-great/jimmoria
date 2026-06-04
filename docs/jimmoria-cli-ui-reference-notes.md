# JIMMORIA CLI UI Reference Notes

## Runtime Dock Update

The current CLI uses a lightweight TUI dock during Research Room execution.

Behavior:

- Logs continue to scroll upward as compact lines: `Room >`, `Agent >`, `Tool >`, `Output >`.
- The old bottom input dock is expanded into a fixed runtime panel.
- The panel contains `JIMMORIA HQ`, provider, room id, aggregate agent progress, and a `Live agent board - current work` table.
- Each agent row shows `WAIT`, `RUN`, `DONE`, or `FAIL`.
- Each agent row shows the current assignment or the latest tool action.
- Tool events update the fixed board in addition to the scrolling log.
- The `> working...` line stays inside the panel, and only the dots blink.

This keeps the CLI usable like a terminal chat while making it clear which agent is currently doing what.

이 문서는 JIMMORIA의 CLI/UI를 개선할 때 참고한 멀티에이전트/zero-human-company 계열 오픈소스와 제품 패턴을 정리한다. 에이전트 내부 구조는 이미 JIMMORIA에 맞게 구성되어 있으므로, 이 문서의 초점은 입력창, 상태 표시, 세션 가시성, Supervisor와 사용자 사이의 대화 경험이다.

## Reference Patterns

| Reference | 참고한 패턴 | JIMMORIA에 적용할 방향 |
|---|---|---|
| [Mato](https://github.com/mr-kelly/mato) / [mato.sh](https://mato.sh/) | offices/desks/tabs 같은 계층형 terminal workspace, live spinner activity, background persistence, theme persistence | Research HQ를 하나의 운영실처럼 보이게 하고, agent 상태를 짧은 live signal로 보여준다. |
| [Conduit](https://github.com/conduit-cli/conduit) / [getconduit.sh](https://getconduit.sh/) | multi-agent TUI, tab-based sessions, real-time streaming, token/cost/status tracking, session persistence | 입력창 주변에 provider, room, agent state 같은 작동 상태를 계속 노출한다. |
| [Spettro](https://github.com/cesp99/spettro) | manifest-driven agent roles, visible handoffs, `/connect`, `/models`, permission modes, live tool traces | Supervisor가 숨겨진 router가 아니라 visible front door가 되게 하고, tool/agent 이벤트를 운영 로그로 보여준다. |
| [Goose](https://github.com/aaif-goose/goose) / [logs guide](https://goose-docs.ai/docs/guides/logs) | CLI 화면은 대화 중심으로 유지하고, tool calls/results/session records/system logs는 로컬 저장소에 남김 | 화면에는 compact event stream만 보여주고, 자세한 이벤트는 `data/runs/<room_id>`와 `/events`로 확인한다. |
| [Agent Cockpit](https://agent-cockpit.dev/) | mission view, agent별 terminal stream, tool/file/approval event timestamps | JIMMORIA도 모든 runtime 이벤트를 큰 카드 대신 `Room >`, `Agent >`, `Tool >`, `Output >` 스트림으로 흘려보낸다. |
| [crewAI](https://github.com/crewAIInc/crewAI) | `agents.yaml`, `tasks.yaml`, `Process.sequential`로 agent와 task를 분리 | JIMMORIA는 `config/processes/*.yaml`로 Research Room task order와 expected output을 분리한다. |
| [MetaGPT](https://github.com/FoundationAgents/MetaGPT) | one-line requirement를 회사 SOP와 role workflow로 전개 | 사용자의 한 문장을 Supervisor가 회사 업무로 해석하고, 필요할 때만 Research Room을 연다. |
| [ChatDev](https://github.com/OpenBMB/ChatDev) | virtual software company, visual workflow/canvas console | 나중에 web visualizer로 agent workflow를 replay할 수 있게 CLI event log를 유지한다. |
| [ZeroHuman](https://zerohuman.sh/) | manifest, profiles, skills/runbooks, predictable artifacts | JIMMORIA도 agents/tools/models/settings를 config와 문서에 남겨 audit 가능한 회사 운영 모델로 유지한다. |

## Current UI Decisions

JIMMORIA의 현재 CLI는 full-screen TUI가 아니라 line-oriented CLI다. 따라서 완전한 fixed bottom input은 아직 구현하지 않고, 다음의 lightweight terminal pattern을 사용한다.

- 입력 전에는 작은 `JIMMORIA HQ` input dock을 그린다.
- input dock에는 `Supervisor channel`, provider, latest room, agent state를 표시한다.
- 사용자가 메시지를 제출하면 ANSI terminal에서는 input dock을 지운다.
- 제출된 문장은 큰 `You` 패널로 반복하지 않고 `You > ...` 로그로 위에 남긴다.
- Supervisor는 바로 `Supervisor > ...` 진행 로그를 남긴 뒤 답변하거나 Research Room을 연다.
- Research Room이 열리면 기본적으로 `Room >`, `Board >`, `Agent >`, `Tool >`, `Output >` compact stream이 이어진다.
- Compact runtime logs include elapsed time and LLM usage, for example `time 1.2s | llm 2 calls / ~4.2k tokens`.
- Research Room 실행 중에도 하단 dock을 유지한다. 새 이벤트가 출력될 때는 이전 dock을 지우고 이벤트를 찍은 뒤 다시 dock을 그려, 사용자가 계속 같은 회사 채팅창 안에 있는 느낌을 준다.
- Research Room 실행 중에는 실제 터미널 커서를 숨기고, dock 내부의 `> working...` 점만 blink 처리한다. 바깥 커서가 박스 밖에서 깜빡이면 안 된다.
- 큰 `Live agent board`와 agent work card는 `/board` 또는 `JIMMORIA_EVENT_STYLE=cards`에서 사용한다.

## Target Shape

```text
+--------------------------------------------------------------------------------+
| JIMMORIA HQ | Supervisor channel | provider: codex_cli | room: none | idle      |
| Type a request, URL, /command, or @path/to/file                                 |
| >                                                                              |
+--------------------------------------------------------------------------------+

You > pearl 프로젝트 리서치 진행해봐
Supervisor > Reading the message, choosing the response shape, and routing the company.

[Supervisor]
  좋아. 이건 리서치 요청이라 Research Room을 열고 에이전트들을 배정할게.

Room > OPEN room_abc123 | agents 10 | pearl 프로젝트 리서치
Board > 10 wait/0 done
Agent > RUN supervisor_agent | Planning direction
Agent > DONE supervisor_agent | Research room initialized | msg 1 / findings 1
Agent > RUN ingestion_agent | Extracting source metadata
Tool > RUN discovery_agent -> web_search | pearl crypto project
+--------------------------------------------------------------------------------+
| JIMMORIA HQ | Supervisor channel | provider: codex_cli | room: room_abc123 ... |
| Room running. Input returns when Supervisor finishes this room.                 |
| > working...                                                                   |
+--------------------------------------------------------------------------------+
```

## GitHub Agent Onboarding Benchmark

이 섹션은 다른 agent/company 계열 GitHub 프로젝트들이 "처음 설치하고 어떻게 시작하는지", "사용자가 어디에 말을 걸고", "작업 로그를 어떻게 보여주는지"를 JIMMORIA 관점으로 정리한 기준이다.

| Project | First install/start pattern | Conversation direction | JIMMORIA decision |
|---|---|---|---|
| [Aider](https://github.com/Aider-AI/aider) / [install docs](https://aider.chat/docs/install.html) | 설치 후 프로젝트 폴더 안에서 바로 `aider`를 실행하고, 같은 터미널 세션에서 파일과 대화한다. | 사용자가 계속 한 채팅창에 요청하고, 도구/변경은 채팅 로그 위로 올라간다. | `jimmoria` 단일 명령으로 HQ에 들어오게 하고, 입력창은 항상 Supervisor 채널로 유지한다. |
| [OpenHands CLI](https://docs.openhands.dev/openhands/usage/cli/quick-start) | CLI quick start가 task 입력, LLM 설정, 실행 모드를 분리한다. | 대화형 CLI, headless, web/server 같은 실행 모드가 분리되어 있다. | 지금은 CLI-first로 두되, 나중에 `jimmoria web`/visual replay가 붙을 수 있도록 `events.json`과 session artifact를 계속 표준화한다. |
| [Goose](https://github.com/block/goose) | 설치 후 `goose`로 세션을 열고, provider/extension/tool 상태를 명령으로 관리한다. | 대화는 짧게 유지하고 tool call/log/session은 별도 저장소와 diagnostics로 뺀다. | 화면에는 compact stream만 보이고, 자세한 agent/tool log는 `data/runs/<room_id>`와 `/events`, `/messages`에서 확인한다. |
| [Hermes Agent](https://hermes-agent.nousresearch.com/docs/getting-started/installation) | installer, device login, gateway, tools, cron, profiles 같은 운영 명령이 분리되어 있다. | 일반 대화와 운영 명령이 공존하지만, 운영 상태는 별도 명령으로 확인한다. | `jimmoria tools`, `cron`, `profile`, `playbook`, `sessions`, `doctor`처럼 회사 운영 명령을 분리한다. |
| [crewAI](https://github.com/crewAIInc/crewAI) / [quickstart](https://docs.crewai.com/quickstart) | `crewai create crew`로 프로젝트를 만들고 agents/tasks YAML을 채운 뒤 `crewai run`으로 실행한다. | 대화형 assistant라기보다 crew/task 실행 프레임워크다. | 에이전트 내부는 유지하고, Research Room의 goals/tasks/expected outputs만 `config/processes/*.yaml`로 분리한다. |
| [ChatDev](https://github.com/OpenBMB/ChatDev) | 자연어 요구사항을 software company workflow와 visual process로 전개한다. | 사용자는 회사에 일을 맡기고, 내부 role/phase가 순차적으로 움직인다. | 사용자는 Supervisor에게 외주를 주고, Supervisor가 Research Room을 열지 직접 대답할지 먼저 판단한다. |
| [MetaGPT](https://github.com/FoundationAgents/MetaGPT) | 한 줄 requirement에서 PM/architect/engineer/reviewer 같은 role workflow를 만든다. | 회사 SOP처럼 요구사항을 role별 산출물로 분해한다. | 리서치 요청은 10개 agent room으로 분해하지만, 잡담/설정/상태 질문은 room을 열지 않는다. |

## Borrowed UX Rules

JIMMORIA에 적용할 기준은 다음이다.

- First run: 사용자는 `jimmoria`만 치면 된다. 설치 안내는 README에 짧게 두고, 첫 화면은 큰 로고와 Supervisor 채팅이다.
- First setup: provider가 없을 때만 모델 설정을 묻는다. 이미 Codex CLI login이 있으면 설정 화면을 건너뛴다.
- Conversation first: 일반 입력은 먼저 Supervisor와 대화한다. Supervisor가 Research Room 필요 여부를 판단한다.
- Confirmation before run: 명확한 연구 작업이라도 Supervisor가 짧게 확인한 뒤 room을 연다. 사용자가 취소하면 run/report artifact를 만들지 않는다.
- Stable input dock: 입력창은 하단 dock처럼 계속 유지되어야 한다. 로그가 올라와도 사용자는 "회사와 대화 중"이라는 감각을 잃지 않아야 한다.
- Compact logs: 실시간 로그는 `Room >`, `Agent >`, `Tool >`, `Output >` 한 줄 stream을 기본값으로 둔다.
- Deep logs elsewhere: 자세한 board, message, event, tool audit은 `/board`, `/messages`, `/events`, `data/runs/<room_id>`로 보낸다.
- Report is not default: 모든 입력을 보고서로 만들지 않는다. 저장된 산출물을 부르는 `3jane 보고서 만들어봐/들고와봐/보내봐` 류는 report lookup으로 처리하고, `새로`, `리서치`, `조사`, `분석`이 있을 때만 새 Research Room을 연다.
- External tools are visible: connector가 없거나 실패하면 조용히 넘어가지 않고, `unconfigured`, `missing evidence`, `insufficient_evidence`를 명확히 표시한다.
- Visual future: CLI event stream은 나중에 ChatDev-style workflow canvas나 web visualizer가 replay할 수 있는 구조로 유지한다.

## Next Onboarding Work

- `jimmoria init`: 첫 설치 후 provider, output language, vault path, preferred workflow를 한 번에 잡는 wizard.
- `jimmoria login`: Codex CLI login 상태를 확인하고 device login 안내를 보여주는 전용 명령.
- `jimmoria doctor --fix`: missing optional dependency나 connector 설정을 가능한 범위에서 안내.
- `jimmoria resume <room_id>`: 이전 Research Room을 다시 열어 Supervisor와 후속 대화.
- `jimmoria --task "<request>"`: OpenHands/MetaGPT처럼 headless one-shot run.
- `jimmoria tui`: 지금 line CLI를 유지하되, 나중에 full-screen bottom-docked input 모드 추가.

## Multi-Task / Parallel Work UI Benchmark

여러 작업을 병행할 때 참고할 UI 기준은 다음이다.

| Reference pattern | What matters | JIMMORIA direction |
|---|---|---|
| Aider-style single chat lane | 사용자는 한 대화창에 계속 입력하고, 시스템이 파일/작업 상태를 위로 올린다. | Supervisor channel은 하나로 유지하고, 여러 room은 `/rooms` workboard에서 선택한다. |
| OpenHands-style task sessions | 각 task는 독립 세션이며, CLI/headless/web 모드가 분리된다. | Research Room은 독립 run artifact를 갖고, 나중에 `resume`/`tui`/`web` 모드가 같은 `events.json`을 읽는다. |
| Goose-style sessions and logs | 대화 화면은 짧게, 자세한 tool/session log는 diagnostics로 분리된다. | 실시간 화면은 compact stream, 자세한 기록은 `/events`, `/messages`, `data/runs/<room_id>`. |
| Hermes-style operations layer | tools, cron, profiles, sessions가 별도 운영 명령으로 분리된다. | 여러 작업은 `cron`, `sessions`, `rooms`, `profile` 명령으로 관리한다. |
| ChatDev/MetaGPT-style company workflow | 여러 role이 움직일 때 phase와 artifact가 보여야 한다. | Workboard는 room 단위, Live board는 agent 단위, event log는 replay 단위로 분리한다. |

현재 적용된 기능:

```text
jimmoria rooms           # recent Research Room workload board
/rooms                   # same board inside Supervisor chat
/work                    # alias
/workboard               # alias
```

Workload board는 각 room을 다음 필드로 요약한다.

```text
STATE     DONE / RUN / FAIL / NEW
ROOM      shortened room_id
PROGRESS  failed/running/waiting/done agent counts
QUALITY   research_complete / insufficient_evidence / unknown
LATEST    latest meaningful room/agent/tool/output event
REPORT    whether a report artifact exists
```

다음 구현 순서:

- Background worker queue: Research Room을 thread/process로 띄우고 Supervisor 채널은 계속 입력 가능하게 만든다.
- `/focus <room_id>`: 특정 room의 live stream만 구독한다.
- `/pause <room_id>` / `/cancel <room_id>`: running room 제어. 실제 connector 붙은 뒤 필요하다.
- `/resume <room_id>`: 이전 room context를 Supervisor 대화에 다시 로드한다.
- Full-screen TUI: top activity strip, central scrollback, bottom input dock, right room/agent board.

## Future UI Backlog

- Full-screen TUI mode: bottom-fixed input, scrollback pane, right-side agent board.
- Session tabs: current room, previous room, source-only room, Daily Radar room.
- Streaming Supervisor replies: live model text stream before final card.
- Tool timeline: each connector call as a collapsible event.
- Replay mode: `events.json`를 사용해 Research Room 진행을 다시 재생.
- Visual web dashboard: ChatDev-style workflow canvas + agent cards + evidence links.
