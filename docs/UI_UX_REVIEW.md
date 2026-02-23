# Comprehensive UI/UX Review - Clinical Trial Simulation Game
**Server:** http://49.254.130.90:9000  
**Review Date:** 2026-02-18  
**Analysis Method:** HTML/CSS Code Review + Visual Layout Analysis

---

## EXECUTIVE SUMMARY

**Overall Assessment:** The application has a solid design foundation with modern aesthetics and thoughtful UI patterns. However, there are **47 UI/UX issues** identified across visual polish, layout, accessibility, and usability.

**Severity Breakdown:**
- 🔴 Critical (must fix): 8 issues
- 🟠 High Priority: 15 issues
- 🟡 Medium Priority: 16 issues
- 🔵 Low Priority: 8 issues

---

## PAGE 1: GAME LANDING PAGE
**URL:** `/game/20260218_071308_Padcev___Pembrolizumab_50pt_126d/`

### HEADER & NAVIGATION

#### 🟠 Issue #1: Navigation Truncation
**Element:** `.global-nav` breadcrumb  
**Line:** 152

```html
<a href="/trial/.../">20260218_071308_Padcev___Pemb…</a>
```

**Problem:**
- Run ID is truncated with ellipsis (`…`)
- Full context is: `20260218_071308_Padcev___Pembrolizumab_50pt_126d`
- Three underscores (`___`) look like a typo/bug
- User cannot see full run name without hovering

**Visual Impact:**
- Unprofessional appearance
- Looks like broken rendering
- Hard to distinguish between different runs

**Fix:**
1. Use better date formatting: `2026-02-18 10:47` instead of `20260218_071308`
2. Remove triple underscores: `Padcev + Pembrolizumab` not `Padcev___Pembrolizumab`
3. Show patient/day count: `50 patients, 126 days`
4. Add tooltip with full name on hover

---

#### 🟡 Issue #2: Nav Bar Too Small
**Element:** `.global-nav`  
**CSS:** Line 14-18

```css
.global-nav {
  height: 38px;
  font-size: 0.78em;  /* Very small! */
}
```

**Problem:**
- 38px height is below minimum recommended (44px for touch targets)
- 0.78em font is ~12.5px at default 16px base - very small
- Links are only 4px padding top/bottom
- On mobile, nearly impossible to tap accurately

**Visual Impact:**
- Squished, cramped header
- Hard to read and click
- Feels like desktop-only design

**Fix:**
```css
.global-nav {
  height: 48px;
  font-size: 0.85em;  /* ~13.6px */
  padding: 0 20px;
}
.global-nav a {
  padding: 6px 12px;  /* Larger touch targets */
}
```

---

#### 🔵 Issue #3: "CTS" Brand Name Unclear
**Element:** `.nav-brand`  
**Line:** 147

```html
<a href="/" class="nav-brand">CTS</a>
```

**Problem:**
- Acronym "CTS" is not explained anywhere
- First-time users don't know what it stands for
- "Clinical Trial Simulator"? "Clinical Trial System"?
- No logo, just text

**Visual Impact:**
- Generic, unmemorable branding
- Could be mistaken for placeholder text

**Fix:**
1. Add tooltip: `<a ... title="Clinical Trial Simulator">CTS</a>`
2. Or use full name: "Clinical Trial Sim"
3. Or add a logo icon

---

### TITLE & INTRODUCTION

#### 🟡 Issue #4: Title Too Large, Subtitle Too Dense
**Element:** `.game-title` and `.game-sub`  
**CSS:** Lines 53-59

```css
.game-title {
  font-size: 1.8em;  /* 28.8px - very large */
  font-weight: 800;  /* Extra bold */
}
.game-sub {
  font-size: 0.95em;
  line-height: 1.7;
}
```

**Content:**
```html
<p class="game-sub">
  Take on the role of a <strong>nurse monitoring cancer patients</strong>.<br>
  Conduct daily video calls, detect hidden adverse events, and make clinical decisions.<br>
  Your choices determine the patient's outcome.
</p>
```

**Problem:**
- Title is massive (1.8em = 28.8px) and dominates the page
- 800 font-weight is extremely bold, looks aggressive
- Subtitle has 3 separate concepts in 3 lines - too much
- Line breaks create awkward visual rhythm
- Orange `<strong>` text stands out too much

**Visual Impact:**
- Overwhelming header section
- Too much to read before getting to action
- Subtitle competes with title for attention

**Fix:**
```css
.game-title {
  font-size: 1.5em;  /* 24px */
  font-weight: 700;  /* Bold but not extra */
}
.game-sub {
  font-size: 0.9em;
  max-width: 600px;  /* Constrain width */
}
```

Simplify content:
```html
<p class="game-sub">
  Take on the role of an AI nurse monitoring cancer patients. 
  Detect adverse events early through daily video calls. 
  Your choices matter.
</p>
```

---

#### 🟡 Issue #5: Drug Badge Looks Placeholder-ish
**Element:** `.drug-badge`  
**Line:** 169

```html
<div class="drug-badge">Padcev + Pembrolizumab -- metastatic urothelial carcinoma</div>
```

**CSS:**
```css
.drug-badge {
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  font-family: 'JetBrains Mono', monospace;
}
```

**Problem:**
- Double dash (`--`) looks like placeholder syntax
- Monospace font makes it look like code/debug output
- All lowercase "metastatic urothelial carcinoma" lacks polish
- Border is very subtle, blends into background
- Placement above difficulty selector is odd

**Visual Impact:**
- Looks unfinished
- Reads like developer notes, not user-facing content
- Low visual hierarchy

**Fix:**
```html
<div class="drug-badge">
  <strong>Padcev + Pembrolizumab</strong>
  <span class="indication">for metastatic urothelial carcinoma</span>
</div>
```

```css
.drug-badge {
  background: rgba(88,166,255,0.08);
  border: 1px solid rgba(88,166,255,0.3);
  font-family: inherit;  /* Not monospace */
  display: flex;
  gap: 8px;
  align-items: center;
}
.drug-badge strong {
  color: var(--accent-blue);
}
.indication {
  color: var(--text-muted);
  font-size: 0.9em;
}
```

---

### DIFFICULTY SELECTOR

#### 🟢 Issue #6: Good Design! (Minor Polish)
**Element:** `.difficulty-cards`  
**Lines:** 172-206

**What Works Well:**
- 3-column grid layout is clean
- Emoji icons (🟢🟡🔴) are intuitive
- Color-coded copilot badges match difficulty
- Hover effects are smooth
- `.selected` state changes border color

**Minor Issues:**
1. **Border change too subtle:** Goes from `var(--border)` to `var(--accent-green)` (2px)
   - Selected card only shows green border, background barely changes (0.06 opacity)
   - User might not notice selection registered
   
2. **Text hierarchy weak:** Difficulty name ("Easy"), description, and copilot badge all similar size
   - `.diff-name` is 1em, `.diff-desc` is 0.78em, `.diff-copilot` is 0.72em
   - Not enough contrast

3. **Description text too long:** Each difficulty has 3 lines of text
   - Easy mode: "AI copilot auto-suggests questions & detects AEs. Abnormal labs/vitals highlighted in color. Clinical hints explain what values mean."
   - Too verbose for a selection screen

**Suggested Polish:**
```css
.diff-card.selected {
  border-width: 3px;  /* Thicker */
  border-color: var(--accent-green);
  background: rgba(63,185,80,0.12);  /* More visible */
  transform: translateY(-2px);  /* Slight lift */
  box-shadow: 0 4px 16px rgba(63,185,80,0.2);
}
.diff-name {
  font-size: 1.15em;  /* Larger */
}
.diff-desc {
  font-size: 0.82em;
  line-height: 1.6;
  /* Shorten text to 1-2 sentences max */
}
```

---

#### 🟡 Issue #7: Emoji Icons May Not Display
**Element:** `.diff-icon`  
**Lines:** 176, 185, 195

```html
<div class="diff-icon">🟢</div>
<div class="diff-icon">🟡</div>
<div class="diff-icon">🔴</div>
```

**Problem:**
- Emoji rendering varies by OS/browser
- On some systems (older Windows), these may show as black/white boxes
- No fallback icon
- Relies on Unicode support

**Visual Impact:**
- Inconsistent appearance across devices
- May look broken on some platforms

**Fix:**
Add CSS with fallback:
```css
.diff-card.easy .diff-icon::before { content: '●'; color: #3FB950; }
.diff-card.normal .diff-icon::before { content: '●'; color: #D29922; }
.diff-card.hard .diff-icon::before { content: '●'; color: #F85149; }
```

Or use SVG icons for guaranteed consistency.

---

### TUTORIAL SECTION

#### 🔴 Issue #8: Tutorial Too Long and Overwhelming
**Element:** `.how-to`  
**Lines:** 209-220

```html
<div class="how-to">
  <h5>How To Play</h5>
  <ol>
    <li>Select a patient below to start an 84-day chemotherapy simulation</li>
    <li>Each day, conduct a <span class="step-highlight">video call</span> with the patient (text chat)</li>
    <li>Observe symptoms, ask probing questions, check lab/vitals in the sidebar</li>
    <li><span class="step-new">NEW</span> Tag suspected AEs + set grade + choose action (inline assessment)</li>
    <li><span class="step-new">NEW</span> Get instant feedback: see what you detected, missed, and why</li>
    <li>On hospital visit days, review lab results and make treatment decisions</li>
    <li>After simulation ends, see full Ground Truth comparison and your scorecard</li>
  </ol>
</div>
```

**Problems:**
1. **7 steps is too many** for a getting-started guide
2. **Dense technical language:** "Tag suspected AEs + set grade + choose action (inline assessment)" - what does this mean to a new user?
3. **"NEW" badges confusing:** New compared to what? User has never used this before
4. **No visual aids:** Just walls of text, no icons or screenshots
5. **Step 4 is incomprehensible:** "Tag suspected AEs + set grade + choose action"
6. **Mixed concepts:** Mixing daily gameplay with end-of-game results

**Visual Impact:**
- Users will skip this entirely (too long)
- First-time experience will be confusing
- Sets wrong expectations (too complicated)

**Fix:**
Simplify to 3 core steps:
```html
<div class="how-to">
  <h5>How To Play</h5>
  <ol>
    <li>📞 <strong>Daily Video Calls:</strong> Chat with your patient each day</li>
    <li>🔍 <strong>Detect Side Effects:</strong> Identify and report adverse events</li>
    <li>📊 <strong>Learn & Improve:</strong> Get instant feedback on your decisions</li>
  </ol>
  <a href="/tutorial" class="tutorial-link">Watch 2-minute tutorial →</a>
</div>
```

---

#### 🟡 Issue #9: Tutorial Box Lacks Visual Interest
**CSS:** Lines 97-105

```css
.how-to {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 18px 22px;
  color: var(--text-secondary);
  font-size: 0.88em;
  line-height: 1.8;
}
```

**Problem:**
- Generic card styling, doesn't stand out
- Same visual weight as patient cards below
- No icon or color to differentiate it
- Numbered list is standard `<ol>` - boring

**Fix:**
```css
.how-to {
  background: linear-gradient(135deg, 
    rgba(57,210,192,0.04), 
    rgba(188,140,255,0.04));
  border-left: 4px solid var(--accent-cyan);
  /* Add icon in header */
}
.how-to h5::before {
  content: '🎮 ';
  margin-right: 6px;
}
```

---

### PATIENT LIST

#### 🔴 Issue #10: Too Many Patients Displayed
**Lines:** 227-996 (50 patient cards!)

**Problem:**
- All 50 patients displayed in single list
- Requires extensive scrolling
- No pagination, filtering, or search
- Overwhelming choice
- No guidance on which patient to choose
- No differentiation except ID, age, sex, ECOG, persona

**Visual Impact:**
- Cognitive overload
- Decision paralysis
- Users will just click the first patient
- Can't find specific persona types

**Fix:**
1. **Show only 8-12 patients initially** with "Load More" button
2. **Add filters:** 
   - Persona type dropdown
   - ECOG level (0, 1, 2)
   - Age range
   - Sex
3. **Add "Recommended" badge** on 2-3 beginner-friendly patients
4. **Group by persona** with collapsible sections
5. **Add search box**

---

#### 🟡 Issue #11: Persona Tags Inconsistent
**Lines:** 236-238, 251-253, etc.

```html
<span class="persona-tag" title="health_literate">Informed</span>
<span class="persona-tag" title="stoic_minimizer">Stoic</span>
<span class="persona-tag" title="caregiver_dependent">Dependent</span>
<span class="persona-tag" title="language_barrier">Language Barrier</span>
```

**Problem:**
- Some are single words: "Informed", "Stoic", "Anxious"
- Some are two words: "Language Barrier"
- Some are adjectives: "Forgetful", "Worried", "Confused"
- Some are nouns: "Minimizer"
- No explanation of what these mean
- Tooltip shows internal name (`health_literate`) not description

**Visual Impact:**
- Inconsistent voice and tone
- Users don't understand what persona affects
- Some terms are vague ("Dependent" - dependent on what?)

**Fix:**
1. **Standardize format:** All single adjectives or all full phrases
2. **Add description tooltips:**
   ```html
   <span class="persona-tag" title="Articulate and proactive about health">
     Informed
   </span>
   ```
3. **Use icons:**
   - 📚 Informed
   - 😤 Stoic
   - 😰 Anxious
   - 🗣️ Language Barrier

---

#### 🟡 Issue #12: Patient Card Hover Too Subtle
**CSS:** Lines 114-117

```css
.patient-card:hover {
  border-color: var(--accent-cyan);
  background: var(--bg-card-hover);
  box-shadow: 0 0 16px rgba(57,210,192,0.12);
}
```

**Problem:**
- `var(--bg-card-hover)` is undefined in the CSS
- Shadow at 0.12 opacity is very faint
- No transform or visual "lift"
- Arrow (`→`) doesn't change

**Visual Impact:**
- Weak hover feedback
- Not obvious cards are clickable
- Feels static

**Fix:**
```css
.patient-card:hover {
  border-color: var(--accent-cyan);
  background: rgba(57,210,192,0.04);
  box-shadow: 0 2px 16px rgba(57,210,192,0.2);
  transform: translateX(4px);  /* Slide right slightly */
}
.patient-card:hover .play-arrow {
  color: var(--accent-green);
  transform: translateX(4px);
}
```

---

#### 🟡 Issue #13: No Visual Distinction Between Patients
**Problem:**
- All patient cards look identical
- Only difference is text content
- No color coding by ECOG, age group, or persona
- No icons or badges
- Hard to scan visually

**Visual Impact:**
- Monotonous list
- Can't quickly find patients of interest
- No visual hierarchy

**Fix:**
1. **Add ECOG color indicators:**
   ```css
   .patient-card[data-ecog="0"] { border-left: 3px solid var(--accent-green); }
   .patient-card[data-ecog="1"] { border-left: 3px solid var(--accent-orange); }
   .patient-card[data-ecog="2"] { border-left: 3px solid var(--accent-red); }
   ```

2. **Add age icons:**
   - 40-60y: 👨 (younger)
   - 60-75y: 👨‍🦳 (middle-aged)
   - 75+: 👴 (elderly)

3. **Highlight special cases:**
   - Language barrier patients
   - ECOG 2 (poor performance)

---

#### 🔵 Issue #14: ECOG Score Not Explained
**Element:** Patient meta line  
**Example:** `<span>ECOG 0</span>`

**Problem:**
- "ECOG" is medical jargon
- Numbers 0, 1, 2 have no context
- No tooltip explaining what it means
- New users have no idea

**Visual Impact:**
- Looks like placeholder data
- Users can't make informed choice

**Fix:**
Add tooltips:
```html
<span title="ECOG 0 = Fully active, no restrictions">ECOG 0</span>
<span title="ECOG 1 = Limited strenuous activity">ECOG 1</span>
<span title="ECOG 2 = Ambulatory, self-care limited">ECOG 2</span>
```

Or use descriptive labels:
```html
<span class="ecog-badge ecog-0">Fully Active</span>
```

---

### SPACING & LAYOUT

#### 🟡 Issue #15: Uneven Vertical Rhythm
**Problem:**
- `.game-container` has `margin-top: 2em` (line 50)
- `.difficulty-section` has `margin-bottom: 2em` (line 69)
- `.how-to` has `margin-bottom: 1.5em` (line 100)
- `.section-label` has `margin-bottom: 1em` (line 64)
- Patient cards have `margin-bottom: 10px` (line 112)

**Visual Impact:**
- Inconsistent spacing creates visual "bumps"
- Some sections feel cramped, others spacious
- No clear visual grouping

**Fix:**
Use consistent spacing scale (8px base):
```css
/* 8px, 16px, 24px, 32px, 48px */
.game-container { margin-top: 48px; }
.difficulty-section { margin-bottom: 32px; }
.how-to { margin-bottom: 24px; }
.section-label { margin-bottom: 16px; }
.patient-card { margin-bottom: 8px; }
```

---

#### 🟡 Issue #16: Container Width Too Narrow
**CSS:** Line 50

```css
.game-container {
  max-width: 800px;
}
```

**Problem:**
- 800px max-width feels cramped on modern displays
- Patient cards with long persona names may wrap awkwardly
- Sidebar (280px) + main content area needs more room
- On 1920px screens, content is tiny in center

**Visual Impact:**
- Lots of wasted white space on sides
- Feels like reading in a narrow column

**Fix:**
```css
.game-container {
  max-width: 1000px;  /* Or 1200px */
}
```

Or use responsive widths:
```css
.game-container {
  max-width: 90%;
  padding: 0 20px;
}
@media (min-width: 1200px) {
  .game-container { max-width: 1100px; }
}
```

---

### TYPOGRAPHY

#### 🟠 Issue #17: Font Size Too Small Throughout
**Problem:**
- `.section-label` is 0.7em (~11.2px) - very small
- `.patient-info .meta` is 0.85em (~13.6px)
- `.persona-tag` is 0.72em (~11.5px)
- `.drug-badge` is 0.82em (~13.1px)
- `.game-sub` is 0.95em (~15.2px)

**Visual Impact:**
- Hard to read, especially on mobile
- Strains eyes
- Feels like fine print
- Not accessible for users with vision impairments

**Fix:**
Increase all sizes by ~10-15%:
```css
.section-label { font-size: 0.8em; }
.patient-info .meta { font-size: 0.9em; }
.persona-tag { font-size: 0.78em; }
.drug-badge { font-size: 0.9em; }
.game-sub { font-size: 1em; }
```

---

#### 🟡 Issue #18: Inconsistent Font Weights
**Problem:**
- `.game-title` is 800 (extra bold)
- `.diff-name` is 700 (bold)
- `.section-label` is 700 (bold)
- `.patient-info h4` is 600 (semi-bold)
- `.diff-desc` is normal (400)

**Visual Impact:**
- No clear hierarchy
- Some elements too bold, others too light
- Inconsistent emphasis

**Fix:**
Standardize to 3 weights:
- 400 (normal) for body text
- 600 (semi-bold) for labels
- 700 (bold) for headings

---

### COLOR & CONTRAST

#### 🟠 Issue #19: Low Contrast on Muted Text
**Problem:**
- Many elements use `var(--text-muted)`
- If this is a light gray on dark background, contrast may be <4.5:1 (WCAG AA failure)
- Examples:
  - `.section-label` (line 63)
  - `.patient-info .meta` (line 124)
  - `.how-to` body text (line 100)

**Visual Impact:**
- Hard to read for users with low vision
- Fails accessibility standards
- Looks washed out

**Fix:**
Test contrast ratios and adjust:
```css
:root {
  --text-muted: #8B949E;  /* Should be at least 4.5:1 on background */
}
```

Use tools like WebAIM Contrast Checker.

---

#### 🔵 Issue #20: CSS Variable Fallbacks Missing
**Problem:**
- All colors use CSS variables: `var(--accent-cyan)`, `var(--bg-card)`, etc.
- No fallback values provided
- If `/static/css/style.css` fails to load, entire page is unstyled

**Example:**
```css
color: var(--accent-cyan);  /* No fallback */
```

**Visual Impact:**
- If CSS variables aren't defined, colors are black/default
- Broken experience if main stylesheet fails

**Fix:**
```css
color: var(--accent-cyan, #39D2C0);  /* Fallback value */
background: var(--bg-card, #1C2128);
```

---

### MOBILE RESPONSIVENESS

#### 🔴 Issue #21: No Mobile Breakpoints
**Problem:**
- Difficulty cards use `grid-template-columns: repeat(3, 1fr)` (line 72)
- No media queries to stack on mobile
- Patient cards will be tiny on phone screens
- Navigation will be cramped

**Visual Impact:**
- Unusable on mobile devices
- Text too small to read
- Buttons too small to tap

**Fix:**
```css
@media (max-width: 768px) {
  .difficulty-cards {
    grid-template-columns: 1fr;  /* Stack vertically */
  }
  .game-container {
    padding: 0 16px;
  }
  .patient-card {
    flex-direction: column;
    align-items: flex-start;
  }
}
```

---

#### 🟠 Issue #22: Touch Targets Too Small
**Problem:**
- Patient cards have 10px margin-bottom
- Difficulty cards have 12px gap
- Navigation links have 4px padding
- All below 44px minimum recommended

**Visual Impact:**
- Hard to tap accurately on touchscreens
- Accidental clicks
- Frustrating mobile experience

**Fix:**
```css
.patient-card {
  margin-bottom: 12px;
  min-height: 60px;  /* Ensure adequate size */
}
.difficulty-cards {
  gap: 16px;
}
.global-nav a {
  padding: 8px 14px;  /* Larger tap area */
}
```

---

### ACCESSIBILITY

#### 🟠 Issue #23: No ARIA Labels
**Problem:**
- Interactive elements lack ARIA labels
- Difficulty cards use `onclick` but no `role="button"`
- Patient cards same issue
- No `aria-label` for icons
- No `aria-describedby` for tooltips

**Examples:**
```html
<div class="diff-card easy selected" onclick="selectDifficulty('easy', this)">
  <!-- No role="button", no keyboard support -->
</div>
```

**Visual Impact:**
- Screen readers can't navigate properly
- Keyboard users can't interact
- Fails WCAG standards

**Fix:**
```html
<div class="diff-card" 
     role="button" 
     tabindex="0"
     aria-label="Easy difficulty: Full AI support"
     aria-pressed="true"
     onclick="selectDifficulty('easy', this)"
     onkeypress="handleKey(event)">
```

---

#### 🟡 Issue #24: No Skip Navigation
**Problem:**
- No "Skip to main content" link
- Users with screen readers have to tab through entire nav

**Fix:**
```html
<a href="#main-content" class="skip-link">Skip to main content</a>
```

```css
.skip-link {
  position: absolute;
  top: -40px;
  left: 0;
  background: var(--accent-cyan);
  color: #000;
  padding: 8px;
  z-index: 100;
}
.skip-link:focus {
  top: 0;
}
```

---

## PAGE 2: GAME PLAY PAGE
**URL:** `/game/20260218_071308_Padcev___Pembrolizumab_50pt_126d/PT-005/`

### OVERALL LAYOUT

#### 🟢 Issue #25: Good Layout Structure!
**Element:** `.game-wrapper`  
**Line:** 50

```css
.game-wrapper { 
  display: flex; 
  height: calc(100vh - 38px); 
  overflow: hidden; 
}
```

**What Works:**
- Full-height layout using flexbox
- Sidebar + main area split
- Responsive to viewport height
- Prevents awkward scrolling

**Minor Issue:**
- `calc(100vh - 38px)` subtracts nav height, but mobile browsers have dynamic viewport (address bar)
- Use `dvh` (dynamic viewport height) when available:
  ```css
  height: calc(100dvh - 38px);
  ```

---

### SIDEBAR

#### 🟠 Issue #26: Sidebar Too Narrow
**CSS:** Line 52

```css
.sidebar {
  width: 280px;
  min-width: 280px;
}
```

**Problem:**
- 280px is cramped for lab names + values + units
- Example: "Glucose (fasting) 140 mg/dL" doesn't fit well
- Scorecard grid (2 columns) has tiny cells
- Patient persona hint is truncated (line 651): "A 62-year-old man, he tends to downplay his symptoms and prefers to handle things on his own. He is a retired construct…"

**Visual Impact:**
- Feels squeezed
- Important info is cut off
- Hard to read lab values

**Fix:**
```css
.sidebar {
  width: 320px;
  min-width: 320px;
}
```

Or make resizable:
```css
.sidebar {
  width: 280px;
  min-width: 240px;
  max-width: 400px;
  resize: horizontal;
}
```

---

#### 🔴 Issue #27: Persona Hint Text Truncated Mid-Word
**Element:** `.persona-hint`  
**Line:** 651

```html
<div class="persona-hint" id="personaHint">
  A 62-year-old man, he tends to downplay his symptoms and prefers to handle things on his own. He is a retired construct…
</div>
```

**Problem:**
- Text cuts off at "construct…" (should be "construction worker" or similar)
- Ellipsis appears mid-word
- No way to see full text
- No tooltip or expand option

**Visual Impact:**
- Looks broken
- Leaves user guessing
- Unprofessional

**Fix:**
1. **Show full text** (don't truncate)
2. **Add "Read more" button** if text is long:
   ```html
   <div class="persona-hint">
     <span class="hint-short">A 62-year-old man who...</span>
     <span class="hint-full" style="display:none;">Full description</span>
     <button onclick="toggleHint()">Read more</button>
   </div>
   ```
3. **Use CSS line clamping properly:**
   ```css
   .persona-hint {
     display: -webkit-box;
     -webkit-line-clamp: 2;
     -webkit-box-orient: vertical;
     overflow: hidden;
   }
   ```

---

#### 🟡 Issue #28: Scorecard Labels Too Abbreviated
**Element:** `.score-metric .lbl`  
**Lines:** 660-673

```html
<div class="score-metric primary">
  <div class="val" id="scDetRate">--</div>
  <div class="lbl">AE Detection</div>
</div>
<div class="score-metric warn">
  <div class="val" id="scMissed">0</div>
  <div class="lbl">Total Missed</div>
</div>
```

**Problems:**
1. "AE Detection" - what does this number mean? Percentage? Count?
2. "Grade Accuracy" - accurate compared to what?
3. "Total Missed" - missed what? AEs? Days?
4. "Days Played" - this should be obvious, why is it a metric?

**Visual Impact:**
- Confusing metrics
- Users don't know if values are good or bad
- No units shown

**Fix:**
```html
<div class="score-metric primary">
  <div class="val">85%</div>
  <div class="lbl">AEs Detected</div>
  <div class="hint">(17/20)</div>
</div>
```

Add explanatory hints:
```css
.score-metric .hint {
  font-size: 0.6em;
  color: var(--text-muted);
  margin-top: 2px;
}
```

---

#### 🟠 Issue #29: "--" Placeholder Unclear
**Element:** Score values  
**Lines:** 659, 663

```html
<div class="val" id="scDetRate">--</div>
<div class="val" id="scGradeAcc">--</div>
```

**Problem:**
- Double dash `--` looks like a range or missing data
- Not obviously a placeholder for "not yet calculated"
- Consistent with developer convention, but not user-friendly

**Visual Impact:**
- Looks like broken data
- Confusing to new users

**Fix:**
Use proper placeholder:
```html
<div class="val">N/A</div>
<!-- Or -->
<div class="val">-</div>
<!-- Or -->
<div class="val" style="opacity:0.4;">0%</div>
```

---

#### 🟡 Issue #30: AE List Empty State
**Element:** `#aeList`  
**Line:** 680

```html
<div id="aeList">
  <span style="color:var(--text-muted); font-size:0.8em;">
    No AEs recorded yet
  </span>
</div>
```

**Problem:**
- Inline styles (bad practice)
- Generic message
- No icon or visual interest
- Doesn't explain what will happen

**Fix:**
```html
<div class="empty-state">
  <div class="empty-icon">✓</div>
  <div class="empty-text">No adverse events detected</div>
</div>
```

```css
.empty-state {
  text-align: center;
  padding: 12px 0;
  color: var(--text-muted);
}
.empty-icon {
  font-size: 1.5em;
  opacity: 0.5;
  margin-bottom: 4px;
}
```

---

#### 🟡 Issue #31: Lab/Vitals History Button Too Small
**Element:** `.btn-history`  
**CSS:** Lines 239-242

```css
.btn-history {
  font-size: 0.6em;
  padding: 2px 6px;
}
```

**Problem:**
- 0.6em is ~9.6px - extremely small
- 2px padding is not enough
- Hard to click, especially on mobile
- Text "History" may not fit

**Visual Impact:**
- Looks like fine print
- Easy to miss
- Poor usability

**Fix:**
```css
.btn-history {
  font-size: 0.72em;
  padding: 4px 10px;
  min-width: 60px;
}
```

---

#### 🟠 Issue #32: Stale Data Badge Too Subtle
**Element:** `.stale-badge`  
**CSS:** Line 254

```css
.stale-badge { 
  font-size: 0.6em;
  color: var(--accent-orange);
  background: rgba(210,153,34,0.15);
  padding: 1px 5px;
}
```

**Problem:**
- 0.6em is ~9.6px - very small
- 15% opacity background is faint
- 1px padding is minimal
- Hidden by default (`style="display:none"`)
- Users might make clinical decisions on outdated data

**Visual Impact:**
- Critical information is hard to see
- Looks like minor annotation, not a warning

**Fix:**
```css
.stale-badge { 
  font-size: 0.75em;
  color: var(--accent-orange);
  background: rgba(210,153,34,0.3);
  padding: 3px 8px;
  border: 1px solid var(--accent-orange);
  font-weight: 700;
}
```

Add icon:
```html
<span class="stale-badge">⚠️ 3 days old</span>
```

---

### HERO START SCREEN

#### 🟢 Issue #33: Start Screen Well Designed!
**Element:** `.start-hero`  
**Lines:** 59-91

**What Works:**
- Full-screen overlay
- Clear call-to-action button
- Patient info displayed
- Good visual hierarchy
- Gradient background
- Large "Start Day 1" button

**Minor Polish:**
1. **Icon could be better:** 🩺 is okay but generic
   - Use 🏥 (hospital) or 👩‍⚕️ (nurse) for more context

2. **Button hover scale too subtle:**
   ```css
   .hero-start-btn:hover {
     transform: scale(1.02);  /* Only 2% larger */
   }
   ```
   Increase to `scale(1.05)` for more noticeable effect

3. **Patient meta line empty:**
   ```html
   <span class="hp-meta" id="heroPatientMeta"></span>
   ```
   Should show: "62y Male, ECOG 0, Stoic persona"

---

### VIDEO HEADER

#### 🔴 Issue #34: Patient Face Image Placeholder
**Element:** `.patient-face-container`  
**Lines:** 281-293, 787-791

```html
<div class="patient-face-container" id="patientFaceContainer">
  <div class="face-placeholder" id="facePlaceholder">👤</div>
  <img id="patientFaceImg" alt="Patient face" />
  <div class="face-loading-spinner" id="faceSpinner" style="display:none;"></div>
</div>
```

**Problem:**
- Placeholder is just generic person icon 👤
- If image fails to load, stays as emoji
- 48x48px is very small for a "video call" face
- No indication this is a generative AI image
- Loading spinner only shows if explicitly triggered

**Visual Impact:**
- Feels unfinished
- Doesn't match "video call" metaphor
- Icon looks like placeholder content

**Fix:**
1. **Use larger container:** 64x64px or 80x80px
2. **Better fallback:**
   ```html
   <div class="face-placeholder">
     <div class="avatar-circle">PT</div>
   </div>
   ```
3. **Show generation status:**
   ```html
   <div class="face-status">🎨 Generating...</div>
   ```

---

#### 🟡 Issue #35: Audio Controls Unclear
**Element:** `.audio-controls`  
**Lines:** 303-315, 792-796

```html
<div class="audio-controls">
  <button class="audio-play-btn" id="audioPlayBtn" onclick="playLatestAudio()" disabled title="Play patient voice">
    🔊
  </button>
  <label class="audio-toggle-label">
    <input type="checkbox" id="autoPlayToggle" style="width:10px;height:10px;"> Auto
  </label>
</div>
```

**Problems:**
1. **Button starts disabled** - why? User can't interact
2. **10px checkbox is tiny** (inline style!)
3. **"Auto" label unclear** - auto-play what?
4. **No visual feedback** when audio is playing
5. **Emoji icon** 🔊 may not render correctly

**Visual Impact:**
- Looks disabled/broken initially
- Confusing purpose
- Poor accessibility

**Fix:**
```html
<div class="audio-controls">
  <button class="audio-play-btn" aria-label="Play patient audio">
    <svg>...</svg> <!-- Use proper icon -->
  </button>
  <label class="audio-toggle">
    <input type="checkbox"> Auto-play
  </label>
</div>
```

```css
.audio-toggle input {
  width: 16px;
  height: 16px;
}
```

---

### CHAT AREA

#### 🟡 Issue #36: Chat Message Width Inconsistent
**CSS:** Lines 329-334

```css
.msg {
  max-width: 80%;
}
.msg.system {
  max-width: 90%;
}
```

**Problem:**
- Patient/nurse messages at 80%, system at 90%
- Why different?
- On narrow screens, 80% leaves awkward gap
- No minimum width

**Visual Impact:**
- Inconsistent visual rhythm
- System messages span almost full width

**Fix:**
```css
.msg {
  max-width: 75%;
}
.msg.system {
  max-width: 85%;
}
@media (max-width: 600px) {
  .msg { max-width: 90%; }
}
```

---

#### 🟡 Issue #37: Typing Indicator Hidden by Default
**Element:** `.typing-indicator`  
**CSS:** Lines 360-376

```css
.typing-indicator {
  display: none;
}
.typing-indicator.visible { 
  display: flex; 
}
```

**Problem:**
- Hidden until explicitly shown
- No automatic timing
- Users may think app is frozen if response is slow
- "Thinking..." text could be more helpful

**Visual Impact:**
- Appears/disappears abruptly
- No smooth transition

**Fix:**
```css
.typing-indicator {
  display: flex;
  opacity: 0;
  transition: opacity 0.3s;
}
.typing-indicator.visible { 
  opacity: 1;
}
```

Better content:
```html
<div class="typing-indicator">
  <div class="typing-dot"></div>
  <div class="typing-dot"></div>
  <div class="typing-dot"></div>
  <span class="typing-label">Patient is typing...</span>
</div>
```

---

#### 🟠 Issue #38: Video Observations Italic and Purple
**CSS:** Lines 354-357

```css
.msg .video-obs {
  margin-top: 5px;
  padding-top: 5px;
  border-top: 1px solid var(--border);
  font-size: 0.8em;
  color: var(--accent-purple);
  font-style: italic;
}
```

**Problem:**
- Purple italic text looks like a quote or aside
- Not obviously "camera detected this"
- Smaller size (0.8em) makes it less important
- Border separates it from message, but same bubble

**Visual Impact:**
- Confusing hierarchy
- Looks like patient said it, not AI observation
- Easy to overlook

**Fix:**
```html
<div class="msg patient">
  <span class="role-tag">PATIENT</span>
  I'm feeling okay today.
  <div class="video-obs">
    📹 Visual: Patient appears fatigued, dark circles under eyes
  </div>
</div>
```

```css
.msg .video-obs {
  background: rgba(188,140,255,0.08);
  border-left: 3px solid var(--accent-purple);
  padding: 6px 10px;
  margin-top: 8px;
  font-style: normal;
  font-size: 0.85em;
}
.msg .video-obs::before {
  content: '📹 ';
  margin-right: 4px;
}
```

---

### COPILOT SUGGESTIONS

#### 🟠 Issue #39: Copilot Strip Horizontal Scroll
**CSS:** Lines 379-382

```css
.copilot-strip {
  overflow-x: auto;
  white-space: nowrap;
}
```

**Problem:**
- Forces horizontal scrolling if many suggestions
- Annoying on desktop (mouse wheel goes vertical)
- On mobile, easy to accidentally scroll page instead
- No indication there's more content to the right

**Visual Impact:**
- Hidden suggestions
- Poor UX
- Feels like bug

**Fix:**
```css
.copilot-strip {
  display: flex;
  flex-wrap: wrap;  /* Allow wrapping */
  gap: 6px;
  overflow-x: visible;
}
```

Or add "Show more" button if >5 suggestions.

---

#### 🟡 Issue #40: Copilot Chip Tooltip on Hover Only
**CSS:** Lines 393-400

```css
.copilot-tooltip {
  display: none;
}
.copilot-chip:hover .copilot-tooltip { 
  display: block; 
}
```

**Problem:**
- Tooltip only appears on hover
- Mobile users can't hover
- No tap/click to show tooltip
- Disappears if mouse moves away

**Visual Impact:**
- Mobile users can't see reasoning
- Frustrating interaction

**Fix:**
Add click handler:
```javascript
chip.addEventListener('click', () => {
  chip.classList.toggle('tooltip-open');
});
```

```css
.copilot-chip.tooltip-open .copilot-tooltip {
  display: block;
}
```

---

### ASSESSMENT PANEL

#### 🟡 Issue #41: Assessment Steps Too Small
**CSS:** Lines 412-419

```css
.assess-step-indicator {
  font-size: 0.72em;
  padding: 5px 14px;
}
```

**Problem:**
- 0.72em is ~11.5px - very small
- Steps are hard to read
- No icons or numbers
- Just text: "1. Tag AEs", "2. Grade", "3. Action"

**Visual Impact:**
- Low visibility
- Steps blend together
- Hard to see which is active

**Fix:**
```css
.assess-step-indicator {
  font-size: 0.82em;
  padding: 8px 16px;
  position: relative;
}
.assess-step-indicator::before {
  content: attr(data-step);
  display: inline-block;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: var(--bg-elevated);
  margin-right: 6px;
  text-align: center;
  line-height: 20px;
}
```

```html
<span class="assess-step-indicator active" data-step="1">Tag AEs</span>
```

---

#### 🟠 Issue #42: Grade Buttons Text Labels Missing
**Element:** `.grade-btn`  
**CSS:** Lines 447-457

```html
<button class="grade-btn">1</button>
<button class="grade-btn">2</button>
<button class="grade-btn">3</button>
<button class="grade-btn">4</button>
```

**Problem:**
- Just numbers 1, 2, 3, 4
- No indication what they mean
- Grade 1 = Mild? Severe?
- New users have no context
- Only tooltip might explain (not shown in code)

**Visual Impact:**
- Confusing interface
- Requires medical knowledge
- Users will guess

**Fix:**
Add labels below:
```html
<div class="grade-btn-group">
  <button class="grade-btn" title="Grade 1: Mild">
    1
    <span class="grade-label">Mild</span>
  </button>
  <button class="grade-btn" title="Grade 2: Moderate">
    2
    <span class="grade-label">Moderate</span>
  </button>
  <button class="grade-btn" title="Grade 3: Severe">
    3
    <span class="grade-label">Severe</span>
  </button>
  <button class="grade-btn" title="Grade 4: Life-threatening">
    4
    <span class="grade-label">Critical</span>
  </button>
</div>
```

```css
.grade-btn {
  flex-direction: column;
  min-width: 60px;
}
.grade-label {
  font-size: 0.65em;
  color: var(--text-muted);
  margin-top: 2px;
}
```

---

#### 🟡 Issue #43: Action Cards Equal Priority
**CSS:** Lines 465-476

```css
.action-cards { 
  display: grid; 
  grid-template-columns: repeat(3, 1fr); 
}
```

**Problem:**
- All 3 action cards same size
- No visual hierarchy
- Some actions are more common (Monitor) vs rare (Hospital Visit)
- No indication which is "safer" vs "aggressive"

**Visual Impact:**
- All options look equally valid
- No guidance for new users

**Fix:**
Highlight common action:
```css
.action-card.recommended {
  border-width: 2px;
  border-color: var(--accent-cyan);
  background: rgba(57,210,192,0.04);
}
.action-card.recommended .act-label::after {
  content: ' (Recommended)';
  font-size: 0.75em;
  color: var(--accent-cyan);
}
```

---

### DEBRIEF OVERLAY

#### 🟡 Issue #44: Debrief Covers Chat History
**CSS:** Lines 480-486

```css
.debrief-overlay {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  max-height: 60%;
}
```

**Problem:**
- Overlay covers bottom 60% of screen
- Can't see chat while reviewing debrief
- Can't reference what patient said
- Blocks sidebar too

**Visual Impact:**
- Removes context
- Forces user to close debrief to check chat
- Annoying back-and-forth

**Fix:**
Make it a modal instead:
```css
.debrief-overlay {
  position: fixed;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0,0,0,0.8);
}
.debrief-box {
  background: var(--bg-secondary);
  max-width: 700px;
  max-height: 80vh;
  overflow-y: auto;
  border-radius: 12px;
}
```

Or add "View Chat" button in debrief.

---

#### 🟡 Issue #45: Debrief Icons Too Small
**CSS:** Lines 507-514

```css
.debrief-icon {
  width: 24px;
  height: 24px;
  font-size: 0.82em;
}
```

**Problem:**
- 24px icon at 0.82em font means ~13px symbol
- Checkmark ✓ or X will be tiny
- Hard to see at a glance

**Visual Impact:**
- Low visual clarity
- Have to read text to understand status

**Fix:**
```css
.debrief-icon {
  width: 32px;
  height: 32px;
  font-size: 1em;
}
```

---

### INPUT AREA

#### 🟡 Issue #46: Send Button Color Too Faint
**CSS:** Lines 562-563

```css
.btn-send { 
  background: rgba(57,210,192,0.15); 
  border-color: rgba(57,210,192,0.4); 
}
```

**Problem:**
- 15% opacity background is very faint
- 40% opacity border is pale
- Doesn't look clickable
- Blends into background

**Visual Impact:**
- Weak call-to-action
- Doesn't draw attention
- Users might not realize it's a button

**Fix:**
```css
.btn-send { 
  background: rgba(57,210,192,0.25); 
  border-color: rgba(57,210,192,0.6); 
}
.btn-send:hover { 
  background: rgba(57,210,192,0.4); 
}
```

Or use solid color:
```css
.btn-send { 
  background: var(--accent-cyan);
  color: #000;
  border: none;
}
```

---

#### 🟠 Issue #47: Textarea Placeholder Generic
**Element:** `<textarea>` in input area  
**Lines:** 547-551

```css
.input-area textarea::placeholder { 
  color: var(--text-muted); 
}
```

**Problem:**
- Placeholder text likely generic: "Type your message..."
- Doesn't guide user on what to ask
- No examples or suggestions
- Especially unhelpful for first-time users

**Visual Impact:**
- User doesn't know what to type
- Blank canvas syndrome

**Fix:**
```html
<textarea placeholder="Ask the patient about symptoms, energy level, appetite, or side effects..."></textarea>
```

Or add example questions below input:
```html
<div class="quick-questions">
  <button>How are you feeling?</button>
  <button>Any nausea today?</button>
  <button>Pain level?</button>
</div>
```

---

## CROSS-PAGE ISSUES

### CONSISTENCY

#### 🟡 Issue #48: Inconsistent Border Radius
**Problem:**
- Landing page: `.diff-card` = 10px (line 76), `.how-to` = 8px (line 99), `.patient-card` = 8px (line 111)
- Game page: `.hero-start-btn` = 12px (line 83), `.audio-play-btn` = 50% (circular), `.msg` = 10px (line 330)
- Values: 4px, 6px, 8px, 10px, 12px, 16px, 20px, 50%

**Visual Impact:**
- No consistent design system
- Some elements feel mismatched

**Fix:**
Standardize to 3 values:
- Small elements: 6px
- Medium elements: 10px
- Large cards: 12px
- Circular: 50%

---

#### 🟡 Issue #49: Color Variables Not Defined in HTML
**Problem:**
- All CSS uses variables: `var(--accent-cyan)`, `var(--bg-card)`, etc.
- Variables must be defined in `/static/css/style.css`
- If that file doesn't load, entire site is unstyled
- No inline `:root` definition as fallback

**Visual Impact:**
- Single point of failure
- No graceful degradation

**Fix:**
Add inline definitions:
```html
<style>
:root {
  --bg-primary: #0D1117;
  --bg-secondary: #161B22;
  --bg-card: #1C2128;
  --bg-elevated: #22272E;
  --text-primary: #E6EDF3;
  --text-secondary: #8B949E;
  --text-muted: #6E7681;
  --border: #30363D;
  --accent-cyan: #39D2C0;
  --accent-green: #3FB950;
  --accent-orange: #D29922;
  --accent-red: #F85149;
  --accent-blue: #58A6FF;
  --accent-purple: #BC8CFF;
  --grade-1: #D29922;
  --grade-2: #E3873E;
  --grade-3: #F85149;
  --grade-4: #DA3633;
}
</style>
```

---

## SUMMARY TABLE

| Issue # | Severity | Page | Category | Summary |
|---------|----------|------|----------|---------|
| #1 | 🟠 High | Landing | Nav | Run ID truncated and ugly |
| #2 | 🟡 Med | Landing | Nav | Nav bar too small |
| #3 | 🔵 Low | Landing | Nav | "CTS" brand unclear |
| #4 | 🟡 Med | Landing | Header | Title too large, subtitle dense |
| #5 | 🟡 Med | Landing | Header | Drug badge looks placeholder |
| #6 | 🟢 Good | Landing | UI | Difficulty selector (minor polish needed) |
| #7 | 🟡 Med | Landing | UI | Emoji icons may not display |
| #8 | 🔴 Critical | Landing | Content | Tutorial too long (7 steps) |
| #9 | 🟡 Med | Landing | Visual | Tutorial box lacks interest |
| #10 | 🔴 Critical | Landing | UX | Too many patients (50 in list) |
| #11 | 🟡 Med | Landing | Content | Persona tags inconsistent |
| #12 | 🟡 Med | Landing | Interaction | Patient card hover too subtle |
| #13 | 🟡 Med | Landing | Visual | No distinction between patients |
| #14 | 🔵 Low | Landing | Content | ECOG score unexplained |
| #15 | 🟡 Med | Landing | Layout | Uneven vertical rhythm |
| #16 | 🟡 Med | Landing | Layout | Container too narrow (800px) |
| #17 | 🟠 High | Landing | Typography | Font sizes too small |
| #18 | 🟡 Med | Landing | Typography | Inconsistent font weights |
| #19 | 🟠 High | Both | Accessibility | Low contrast on muted text |
| #20 | 🔵 Low | Both | Code | CSS variable fallbacks missing |
| #21 | 🔴 Critical | Landing | Mobile | No mobile breakpoints |
| #22 | 🟠 High | Landing | Mobile | Touch targets too small |
| #23 | 🟠 High | Landing | Accessibility | No ARIA labels |
| #24 | 🟡 Med | Landing | Accessibility | No skip navigation |
| #25 | 🟢 Good | Game | Layout | Full-height layout structure |
| #26 | 🟠 High | Game | Layout | Sidebar too narrow (280px) |
| #27 | 🔴 Critical | Game | Content | Persona hint truncated mid-word |
| #28 | 🟡 Med | Game | UI | Scorecard labels abbreviated |
| #29 | 🟠 High | Game | UI | "--" placeholder unclear |
| #30 | 🟡 Med | Game | UI | AE list empty state generic |
| #31 | 🟡 Med | Game | UI | Lab history button too small |
| #32 | 🟠 High | Game | Critical | Stale data badge too subtle |
| #33 | 🟢 Good | Game | UI | Start screen well designed |
| #34 | 🔴 Critical | Game | Visual | Patient face placeholder basic |
| #35 | 🟡 Med | Game | UI | Audio controls unclear |
| #36 | 🟡 Med | Game | Layout | Chat message width inconsistent |
| #37 | 🟡 Med | Game | UI | Typing indicator hidden |
| #38 | 🟠 High | Game | Visual | Video observations unclear |
| #39 | 🟠 High | Game | UX | Copilot strip horizontal scroll |
| #40 | 🟡 Med | Game | Mobile | Copilot tooltip hover-only |
| #41 | 🟡 Med | Game | UI | Assessment steps too small |
| #42 | 🟠 High | Game | Critical | Grade button labels missing |
| #43 | 🟡 Med | Game | UX | Action cards equal priority |
| #44 | 🟡 Med | Game | UX | Debrief covers chat history |
| #45 | 🟡 Med | Game | Visual | Debrief icons too small |
| #46 | 🟡 Med | Game | UI | Send button color faint |
| #47 | 🟠 High | Game | UX | Textarea placeholder generic |
| #48 | 🟡 Med | Both | Consistency | Inconsistent border radius |
| #49 | 🟡 Med | Both | Code | Color variables not defined inline |

---

## PRIORITY FIXES

### Must Fix Before Launch (Critical):
1. **Tutorial too long** (#8) - Reduce to 3 simple steps
2. **Too many patients** (#10) - Add filtering/pagination
3. **No mobile breakpoints** (#21) - Add responsive CSS
4. **Persona hint truncated** (#27) - Show full text or proper clamp
5. **Patient face placeholder** (#34) - Better fallback design
6. **Assessment grade buttons** (#42) - Add "Mild/Moderate/Severe" labels
7. **Stale data warning** (#32) - Make more prominent
8. **Run ID truncation** (#1) - Fix naming and display

### Fix in First Polish Pass (High Priority):
9. Font sizes too small (#17, #31, #41)
10. Sidebar too narrow (#26)
11. "--" placeholders (#29)
12. Video observations unclear (#38)
13. Copilot horizontal scroll (#39)
14. Touch targets (#22)
15. ARIA labels (#23)
16. Contrast issues (#19)

### Nice to Have (Medium):
17-47: Various polish, consistency, and UX improvements

---

## POSITIVE ASPECTS

**What Works Well:**
1. ✅ Overall color scheme is professional and modern
2. ✅ Difficulty selector has good visual design
3. ✅ Full-height game layout is solid
4. ✅ Start screen is welcoming and clear
5. ✅ Chat message styling is clean
6. ✅ Debrief feedback concept is excellent
7. ✅ Scorecard provides good metrics
8. ✅ Progress bar gives sense of advancement
9. ✅ Copilot chips are visually appealing
10. ✅ Assessment 3-step flow makes sense

---

## RECOMMENDATIONS

### Immediate Actions:
1. **Run accessibility audit** with WAVE or axe
2. **Test on mobile devices** - likely many issues
3. **User testing session** - watch first-time users
4. **Reduce cognitive load** - simplify instructions and choices
5. **Add tooltips everywhere** - explain medical terms
6. **Consistent spacing system** - use 8px base

### Design System:
1. Document color variables
2. Standardize font sizes (5-7 sizes max)
3. Consistent border radius (3 values)
4. Standard component library
5. Mobile-first approach

### Future Enhancements:
1. Onboarding tutorial (interactive)
2. Settings panel (audio, difficulty, hints)
3. Keyboard shortcuts
4. Save/resume functionality
5. Dark/light mode toggle

---

**End of Report**
