# JIMMORIA CLI UI Reference Notes

이 문서는 JIMMORIA의 CLI/UI를 개선할 때 참고한 멀티에이전트/zero-human-company 계열 오픈소스와 제품 패턴을 정리한다. 에이전트 내부 구조는 이미 JIMMORIA에 맞게 구성되어 있으므로, 이 문서의 초점은 입력창, 상태 표시, 세션 가시성, Supervisor와 사용자 사이의 대화 경험이다.

## Reference Patterns

| Reference | 참고한 패턴 | JIMMORIA에 적용할 방향 |
|---|---|---|
| [Mato](https://github.com/mr-kelly/mato) / [mato.sh](https://mato.sh/) | offices/desks/tabs 같은 계층형 terminal workspace, live spinner activity, background persistence, theme persistence | Research HQ를 하나의 운영실처럼 보이게 하고, agent 상태를 짧은 live signal로 보여준다. |
| [Conduit](https://github.com/conduit-cli/conduit) / [getconduit.sh](https://getconduit.sh/) | multi-agent TUI, tab-based sessions, real-time streaming, token/cost/status tracking, session persistence | 입력창 주변에 provider, room, agent state 같은 작동 상태를 계속 노출한다. |
| [Spettro](https://github.com/cesp99/spettro) | manifest-driven agent roles, visible handoffs, `/connect`, `/models`, permission modes, live tool traces | Supervisor가 숨겨진 router가 아니라 visible front door가 되게 하고, tool/agent 이벤트를 운영 로그로 보여준다. |
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
- Research Room이 열리면 `Live agent board`, agent work card, tool activity card가 이어진다.

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

[Live agent board]
  RUN      ingestion_agent              Now: Extracting source metadata
  WAIT     narrative_agent              Waiting: Mapping narratives
```

## Future UI Backlog

- Full-screen TUI mode: bottom-fixed input, scrollback pane, right-side agent board.
- Session tabs: current room, previous room, source-only room, Daily Radar room.
- Streaming Supervisor replies: live model text stream before final card.
- Tool timeline: each connector call as a collapsible event.
- Cost/token meter: provider-level call count, token estimate, latency.
- Replay mode: `events.json`를 사용해 Research Room 진행을 다시 재생.
- Visual web dashboard: ChatDev-style workflow canvas + agent cards + evidence links.

