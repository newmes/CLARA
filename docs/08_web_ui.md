# 08. Web UI — Trial Viewer + Game Interface

> **프레임워크:** Django + Vanilla JS + Phaser 3
> **디자인:** 다크 모드 (GitHub Dark 스타일 CSS 변수)
> **데이터:** 파일 기반 (JSONL, JSON) — DB 없음
>
> 🔗 **웹에서 확인:**
> | 페이지 | URL |
> |--------|-----|
> | 랜딩 페이지 | [http://49.254.130.90:9000/](http://49.254.130.90:9000/) |
> | Trial Viewer | [http://49.254.130.90:9000/trial/20260216_062601.../](http://49.254.130.90:9000/trial/20260216_062601_Padcev___Pembrolizumab_10pt_84d/) |
> | Patient Detail | [http://49.254.130.90:9000/patient/20260216.../PT-001/](http://49.254.130.90:9000/patient/20260216_062601_Padcev___Pembrolizumab_10pt_84d/PT-001/) |
> | Game Landing | [http://49.254.130.90:9000/game/20260216.../](http://49.254.130.90:9000/game/20260216_062601_Padcev___Pembrolizumab_10pt_84d/) |
> | Game Play | [http://49.254.130.90:9000/game/20260216.../PT-001/](http://49.254.130.90:9000/game/20260216_062601_Padcev___Pembrolizumab_10pt_84d/PT-001/) |

---

## 1. 기술 스택

| 레이어 | 기술 |
|--------|------|
| Backend | Django (Python) |
| CSS | 커스텀 다크 모드 CSS 변수 + Bootstrap 3.4.1 |
| JavaScript | jQuery 3.7.1 + Vanilla JS |
| Game Tilemap | Phaser 3.55.2 (2D pixel-art) |
| 폰트 | Inter (본문), JetBrains Mono (데이터/코드) |
| 실시간 | Server-Sent Events (SSE) |

---

## 2. URL 라우팅

### 페이지 뷰 (HTML)

| URL | View 함수 | 설명 |
|-----|----------|------|
| `/` | `landing` | 시뮬레이션 run 선택 |
| `/trial/<run_id>/` | `trial_viewer` | Day-by-day 시뮬레이션 뷰어 |
| `/trial/<run_id>/<day>/` | `trial_viewer` | 특정 날짜 뷰어 |
| `/patient/<run_id>/<pid>/` | `patient_state` | 환자 개별 상세 뷰 |
| `/patient/<run_id>/<pid>/<day>/` | `patient_state` | 환자 특정일 뷰 |
| `/game/<run_id>/` | `game_landing` | 게임 모드 환자 선택 |
| `/game/<run_id>/<pid>/` | `game_play` | 인터랙티브 게임 플레이 |

### JSON API

| URL | 메서드 | 설명 |
|-----|--------|------|
| `/api/run/<run_id>/` | GET | Run 메타데이터 (환자, 약물, 총 일수) |
| `/api/day/<run_id>/<day>/` | GET | 모든 환자의 해당일 데이터 |
| `/api/patient/<run_id>/<pid>/` | GET | 환자 전체 타임라인 |
| `/api/sse/<run_id>/` | GET | SSE 자동 재생 스트림 |

### 게임 API

| URL | 메서드 | 설명 |
|-----|--------|------|
| `/api/game/start` | POST | 세션 생성 |
| `/api/game/advance` | POST | 하루 전진 |
| `/api/game/greet` | POST | 환자 첫 인사 |
| `/api/game/chat` | POST | 채팅 메시지 교환 |
| `/api/game/end-chat` | POST | 관찰/행동 제출 |
| `/api/game/skip` | POST | 대화 없이 건너뛰기 |
| `/api/game/reveal/<sid>/` | GET | GT 공개 + 성적표 |
| `/api/game/sessions` | GET | 활성 세션 목록 |

---

## 3. 템플릿 구조

### 3.1 Base Template (`base/base.html`)

```html
<body>
  <nav class="global-nav">            <!-- 38px 고정 네비게이션 -->
    <span class="brand">CTS</span>
    <a href="/">Runs</a>
    {% block nav_items %}{% endblock %}  <!-- 페이지별 breadcrumb -->
  </nav>
  {% block content %}{% endblock %}      <!-- 메인 콘텐츠 -->
  {% block js_content %}{% endblock %}   <!-- 하단 JavaScript -->
</body>
```

**body padding-top: 38px** → nav bar가 콘텐츠를 가리지 않도록.

### 3.2 상속 트리

```
base.html
├── landing.html          nav: "All Runs" (active)
├── trial.html            nav: run_id / "Day View" (active) / "Play Game"
├── patient_state.html    nav: run_id / Day# / patient_id
├── game_landing.html     nav: run_id / "Play Game" (active) / "Day View"
└── game_play.html        nav: run_id / "Play Game" / patient_id / cross-links
```

---

## 4. Trial Viewer 상세

### 4.1 레이아웃

**이중 뷰 탭 구조:**

1. **Map View** (기본): Phaser 3 pixel-art 타일맵
   - 병원 건물 + 10개 환자 집
   - 환자 스프라이트가 location에 따라 이동
   - AE severity에 따른 상태 도트 (초록/노랑/주황/빨강)
   - 클릭으로 환자 상세 표시

2. **Dashboard View**: 3-섹션 레이아웃
   - **Events Panel:** severity 순 정렬된 이벤트 피드
   - **Patient Cards Grid:** 5열 환자 카드 (클릭 가능)
   - **Patient Detail Panel:** 3열 상세 (임상 상태 / Labs+Vitals / Perception+Mood)

### 4.2 Day 내비게이션

```
[◀ Prev] [Day Slider ═══════════●═══] [Next ▶]  [▶ Auto-Play] [Speed: x1/x5/x10]
```

- **Range slider:** 마우스 드래그로 날짜 이동
- **키보드:** ← → 화살표
- **Auto-Play (SSE):** `EventSource` → `/api/sse/<run_id>/` → 서버가 day 이벤트를 speed에 맞춰 push

### 4.3 GT/HR 토글

```
[Ground Truth ●──── Hospital Record]
```

클라이언트 사이드 토글:
- GT 모드: `patient.active_aes` (모든 AE, 정확한 grade)
- HR 모드: `patient.hospital_record.objective.active_aes` (감지된 AE만, distortion 적용)
- Detection gap 카운트 표시 (GT에는 있지만 HR에는 없는 AE 수)

### 4.4 Mode 전환

Natural / Care AI 버튼 → `?mode=` 쿼리 파라미터로 페이지 리로드.

### 4.5 Care AI 채팅 표시

4-Turn 구조 렌더링:
- T1: 환자 말풍선 (왼쪽)
- T2: 간호사 말풍선 (오른쪽)
- T3: 환자 말풍선 (왼쪽)
- T4: 간호사 판정 카드 (severity 색상 + action 목록)

### 4.6 Mood 바 차트

7차원 각각에 대해 가로 바:
- anxiety, irritability, defensiveness: 높을수록 빨강
- energy, cognitive_clarity, trust_in_ai: 높을수록 초록
- depression: 높을수록 보라

---

## 5. Patient State 뷰

**환자 1명의 전체 시뮬레이션 데이터를 종단적으로 표시:**

- AE 타임라인 (onset → grade 변화 → resolution)
- Lab 추세 (ANC, Hb, PLT, creatinine 등 시계열)
- Mood 궤적 (7차원 × 일수)
- 이벤트 로그 (Generative Agents 스타일 메모리)
- 환자 프로필 (demographics, persona, comorbidities)

---

## 6. Game Play UI 상세

### 6.1 레이아웃

```
┌──────────────────────────────────────────────────────┐
│ Global Nav (38px)                                    │
├───────────┬──────────────────────────────────────────┤
│ Sidebar   │ Top Bar: Day N | Cycle 1 Day 5 | ●      │
│ (300px)   ├──────────────────────────────────────────┤
│           │ Progress Bar ████████░░░░░░░░ 25/84      │
│ ┌───────┐ ├──────────────────────────────────────────┤
│ │Known  │ │ Chat Area (scrollable)                   │
│ │ AEs   │ │                                          │
│ │       │ │ [System] Day 5 시작 | 병원: 아님          │
│ │nausea │ │                                          │
│ │ Gr.1  │ │ [Patient] 안녕하세요, 간호사님...          │
│ │       │ │           좀 속이 안 좋아요               │
│ ├───────┤ │                                          │
│ │ Labs  │ │ [Nurse] 피부에 변화가 있으신가요?          │
│ │       │ │                                          │
│ │ANC:4.2│ │ [Patient] 음... 사실 팔에 좀 빨간 게...   │
│ │Hb:13.1│ │                                          │
│ ├───────┤ ├──────────────────────────────────────────┤
│ │Vitals │ │ Observation Tags: [fatigue] [nausea]     │
│ │       │ │  [rash] [neuropathy] [+custom]           │
│ │BT:36.7│ ├──────────────────────────────────────────┤
│ │HR:76  │ │ [Advance Day] [Skip] [End & Submit]     │
│ ├───────┤ ├──────────────────────────────────────────┤
│ │ Tx    │ │ ┌─────────────────────────────────────┐  │
│ │Status │ │ │ Message: [                        ] │  │
│ │●On Tx │ │ │                           [Send ▶] │  │
│ ├───────┤ │ └─────────────────────────────────────┘  │
│ │Score  │ │                                          │
│ │ 16/30 │ │                                          │
└───────────┴──────────────────────────────────────────┘
```

### 6.2 Sidebar 업데이트

**HR 전용 데이터 표시:**
```javascript
function updateSidebar(hr) {
    // hr = hospital_record.objective
    // AEs: hr.active_aes (감지된 것만, grade distortion 적용)
    // Labs: hr.labs (방문 없으면 stale)
    // Vitals: hr.vitals (방문 없으면 stale)
    // Treatment: hr.treatment_status
}
```

**Staleness 표시:** Labs와 vitals가 오래되면 시각적으로 표시 (opacity 감소 등).

### 6.3 게임 흐름 (JavaScript)

```javascript
async function startGame() {
    const res = await fetch('/api/game/start', {
        method: 'POST',
        body: JSON.stringify({run_id, patient_id, total_days: 84, seed: random})
    });
    sessionId = res.session_id;
    await advanceDay();
}

async function advanceDay() {
    const day = await fetch('/api/game/advance', {body: {session_id}});
    updateSidebar(day.hospital_record);
    
    const greet = await fetch('/api/game/greet', {body: {session_id}});
    appendChatBubble('patient', greet.display_text);
    
    if (greet.video_visible.length > 0) {
        appendSystemMessage(`📹 영상에서 관찰: ${greet.video_visible.join(', ')}`);
    }
}

async function sendChat() {
    const message = inputArea.value;
    appendChatBubble('nurse', message);
    
    const res = await fetch('/api/game/chat', {body: {session_id, message}});
    appendChatBubble('patient', res.display_text);
    
    if (res.video_visible.length > 0) {
        appendSystemMessage(`📹 추가 관찰: ${res.video_visible.join(', ')}`);
    }
}

async function submitDecision() {
    const observations = collectObservationTags();
    const actions = collectActionSelections();
    
    const res = await fetch('/api/game/end-chat', {
        body: {session_id, observations, actions}
    });
    
    if (res.is_finished) {
        await revealGT();
    }
}
```

### 6.4 Decision Modal

**AE 관찰 입력:**
- 10개 사전 정의 AE 태그 (클릭 선택)
- 커스텀 AE 추가 가능
- 각 AE에 예상 grade (1-4) 선택
- detection source (patient_report / video / inference)

**Action 선택:**
- `no_action`: 관찰 없음, 다음 날로
- `monitor_closely`: 주의 관찰
- `recommend_conmed`: 보조약 추천 (세부사항 입력)
- `recommend_early_visit`: 조기 방문 권유
- `recommend_hospital_visit`: 당일 병원 방문
- `escalate_to_physician`: 의사에게 에스컬레이션

### 6.5 성적표 오버레이 (Reveal GT)

```
┌─────────────────────────────────────────────────┐
│ ★ Ground Truth Revealed                         │
├─────────────────────────────────────────────────┤
│ AE          │ GT 발생일 │ 감지일 │ 지연 │ 점수  │
│ nausea      │  Day 3    │ Day 5  │  2d  │ 8/10  │
│ rash        │  Day 12   │ Day 14 │  2d  │ 8/10  │
│ neuropathy  │  Day 42   │  ✗     │  —   │ 0/10  │
├─────────────────────────────────────────────────┤
│ 총점: 16/30 (53.3%)                             │
│ 발생 AE: 3개 | 감지: 2개 | 미감지: 1개          │
│ 종양 반응: PR | ECOG 변화: 1 → 2                 │
└─────────────────────────────────────────────────┘
```

---

## 7. 데이터 흐름

```
data/runs/{run_id}/
├── rule_set.json              ← 약물 규칙 (cycle_length 등)
├── patients/PT-001.json       ← 환자 프로필
└── simulations/
    ├── PT-001_natural.jsonl   ← Day별 1줄, 모든 도메인 데이터
    └── PT-001_care_ai.jsonl   ← Care AI 모드 데이터
```

**JSONL 한 줄 구조:**
```json
{
  "day": 5, "patient_id": "PT-001",
  "objective": {"location", "treatment_status", "ecog", "tumor", "labs", "vitals"},
  "subjective": {"overall_awareness", "symptoms_patient_perceives"},
  "AE": [...], "EC": [...], "CM": [...], "LB": {...}, "VS": {...},
  "RS": {...}, "DS": {...}, "DD": {...},
  "care_record": [...],
  "hospital_record": {...},
  "observation_events": [...],
  "mood_state": {...},
  "_sim": {"generation_mode": "event_day|quiet_day", "mortality_risk": 0.0012}
}
```

**DB 없음:** 모든 데이터는 파일에서 직접 파싱. `_load_day_for_patient()`, `_load_all_days_for_patient()` 헬퍼 사용.

---

## 8. 글로벌 네비게이션

모든 페이지에서 양방향 이동 가능:

```
Landing (/) ←─────────→ Trial Viewer (/trial/)
     │                       ↑↓
     └──→ Game Landing ←───→ Trial Viewer
              │
              └──→ Game Play ←──→ Patient State
```

각 페이지의 nav_items에 현재 위치 + 크로스 링크가 포함됨.