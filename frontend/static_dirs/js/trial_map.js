/**
 * Clinical Trial Map — Dynamic Kenney Tiny Town tileset
 *
 * Loads map_meta.json for dynamic home/hospital positions and waypoint graph.
 * Uses BFS pathfinding along waypoints for character movement.
 */

const TILE = 16;
const STATIC = '/static/assets';

const STATUS_COLORS = {
  severe:   0xf85149,
  moderate: 0xd29922,
  normal:   0x3fb950,
};

const AWARENESS_MESSAGES = {
  'EMERGENCY': [
    '살려주세요...!', '너무 아파요...', '숨을 못 쉬겠어요',
    '의사 선생님!', '119 불러주세요'
  ],
  'DISTRESSED': [
    '많이 힘들어요...', '견디기 힘든데...', '좀 무서워요',
    '이거 정상인가요?', '계속 심해지는데...'
  ],
  'CONCERNED': [
    '좀 걱정이 돼요', '이거 괜찮을까?', '뭔가 좀 이상해요',
    '선생님한테 물어봐야 하나', '가려운데...'
  ],
  'NOTICED': [
    '좀 불편하긴 한데...', '별 거 아니겠지', '참을 만해요',
    '뭐 그럴 수도 있지', '약간 피곤해요'
  ],
};

class TrialMap {
  constructor(containerId, patients, onPatientClick, runId) {
    this.patients = patients || [];
    this.onPatientClick = onPatientClick;
    this.runId = runId;
    this.sprites = {};
    this.nameLabels = {};
    this.statusDots = {};
    this.speechBubbles = {};
    this.bubbleTimers = {};
    this._scene = null;
    this._containerId = containerId;
    this.onPhoneCall = null;
    this._animating = false;  // true while movement tweens are active
    this._animDoneResolve = null;

    // Dynamic map data (loaded from API)
    this.mapMeta = null;
    this.homePositions = {};
    this.hospitalPos = null;
    this.waypointGraph = {};

    const el = document.getElementById(containerId);
    const w = el ? el.clientWidth : 1200;

    this._loadMapMeta().then(() => {
      this.game = new Phaser.Game({
        type: Phaser.AUTO,
        width: w,
        height: 580,
        parent: containerId,
        backgroundColor: '#2d5a1e',
        pixelArt: true,
        scene: {
          preload: this._preload.bind(this),
          create:  this._create.bind(this),
          update:  this._update.bind(this),
        },
      });
    });
  }

  async _loadMapMeta() {
    try {
      const resp = await fetch(`/api/map/${this.runId}/`);
      if (resp.ok) {
        this.mapMeta = await resp.json();
        this.hospitalPos = this.mapMeta.hospital;

        const homes = this.mapMeta.homes || [];
        homes.forEach(h => {
          this.homePositions[h.patient_idx] = h;
        });

        (this.mapMeta.waypoints || []).forEach(wp => {
          this.waypointGraph[wp.id] = {
            x: wp.x, y: wp.y,
            neighbors: wp.neighbors || [],
          };
        });
      }
    } catch (e) {
      console.warn('Failed to load map metadata, using defaults:', e);
    }

    if (!this.hospitalPos) {
      this.hospitalPos = { x: 4, y: 14, waypoint_id: 'hosp' };
    }
  }

  _getHomePos(patientIdx) {
    if (this.homePositions[patientIdx]) {
      const h = this.homePositions[patientIdx];
      return { x: h.x, y: h.y };
    }
    const col = patientIdx % 5;
    const row = Math.floor(patientIdx / 5);
    return { x: 17 + col * 5, y: 6 + row * 20 };
  }

  // ─── BFS Pathfinding ───────────────────────────────────
  findPath(fromWpId, toWpId) {
    if (!fromWpId || !toWpId || fromWpId === toWpId) return [];
    if (!this.waypointGraph[fromWpId] || !this.waypointGraph[toWpId]) return [];

    const queue = [[fromWpId, [fromWpId]]];
    const visited = new Set([fromWpId]);

    while (queue.length > 0) {
      const [current, path] = queue.shift();
      if (current === toWpId) {
        return path.map(id => ({
          id,
          x: this.waypointGraph[id].x * TILE + TILE / 2,
          y: this.waypointGraph[id].y * TILE + TILE / 2,
        }));
      }
      for (const nb of (this.waypointGraph[current]?.neighbors || [])) {
        if (!visited.has(nb)) {
          visited.add(nb);
          queue.push([nb, [...path, nb]]);
        }
      }
    }
    return [];
  }

  // ─── Preload ─────────────────────────────────────────
  _preload() {
    const s = this.game.scene.scenes[0];
    // Cache-bust: append timestamp from map_meta to prevent stale tilemap
    const cacheBust = this.mapMeta ? `?v=${this.mapMeta.n_patients}` : `?v=${Date.now()}`;
    s.load.tilemapTiledJSON('map', `${STATIC}/map/clinical_trial.json${cacheBust}`);
    s.load.image('tiny-town', `${STATIC}/kenney/tiny-town/Tilemap/tilemap_packed.png`);

    for (let i = 1; i <= 10; i++) {
      const key = `patient_${String(i).padStart(2, '0')}`;
      s.load.spritesheet(key, `${STATIC}/characters/${key}.png`, {
        frameWidth: 32, frameHeight: 32,
      });
    }
  }

  // ─── Create ──────────────────────────────────────────
  _create() {
    this._scene = this.game.scene.scenes[0];
    const scene = this._scene;

    const map = scene.make.tilemap({ key: 'map' });
    const tileset = map.addTilesetImage('tiny-town', 'tiny-town', 16, 16, 0, 0);
    if (!tileset) { console.error('Failed to load tileset'); return; }

    ['Ground', 'Buildings', 'Roofs', 'Decor'].forEach((name, i) => {
      try {
        const layer = map.createLayer(name, tileset, 0, 0);
        if (layer) layer.setDepth(i);
      } catch (e) { console.warn(`Layer "${name}":`, e.message); }
    });

    // Camera
    const camera = scene.cameras.main;
    camera.setBounds(0, 0, map.widthInPixels, map.heightInPixels);
    const zoomX = this.game.config.width / map.widthInPixels;
    const zoomY = this.game.config.height / map.heightInPixels;
    const fitZoom = Math.min(zoomX, zoomY) * 0.95;
    camera.setZoom(Math.max(fitZoom, 1.0));
    // Center on hospital
    camera.centerOn(
      this.hospitalPos.x * TILE + TILE / 2,
      this.hospitalPos.y * TILE + TILE / 2
    );

    // Drag to pan
    scene.input.on('pointermove', (pointer) => {
      if (pointer.isDown) {
        camera.scrollX -= (pointer.x - pointer.prevPosition.x) / camera.zoom;
        camera.scrollY -= (pointer.y - pointer.prevPosition.y) / camera.zoom;
      }
    });
    scene.input.on('wheel', (p, go, dx, dy) => {
      camera.setZoom(Phaser.Math.Clamp(camera.zoom - dy * 0.003, 0.5, 5.0));
    });

    this._createAnimations(scene);

    // Place patients
    this.patients.forEach((p, i) => {
      const spriteIdx = (i % 10) + 1;
      const spriteKey = `patient_${String(spriteIdx).padStart(2, '0')}`;
      const home = this._getHomePos(i);

      const loc = (p.location || '').toUpperCase();
      const isHosp = loc === 'HOSPITAL' || loc === 'OUTPATIENT' || loc === 'INPATIENT';
      const pos = isHosp ? this.hospitalPos : home;
      const px = pos.x * TILE + TILE / 2 + (isHosp ? (i % 5 - 2) * 12 : 0);
      const py = pos.y * TILE + TILE / 2 + (isHosp ? Math.floor(i / 5) * 12 : 0);

      const sprite = scene.add.sprite(px, py, spriteKey, 1);
      sprite.setScale(0.5);
      sprite.setDepth(10);
      sprite.setInteractive({ useHandCursor: true });
      sprite.on('pointerdown', () => {
        if (this.onPatientClick) this.onPatientClick(p.patient_id);
        this.highlightPatient(p.patient_id);
      });

      const label = scene.add.text(px, py - 12, p.patient_id, {
        font: 'bold 5px monospace', fill: '#ffffff',
        stroke: '#000000', strokeThickness: 2, align: 'center',
      });
      label.setOrigin(0.5, 1).setDepth(20);

      const color = this._getStatusColor(p);
      const dot = scene.add.circle(px + 6, py - 6, 2, color);
      dot.setDepth(21).setStrokeStyle(1, 0x000000);

      const homeWpId = this.homePositions[i]?.waypoint_id || null;
      sprite.setData('homeX', home.x * TILE + TILE / 2);
      sprite.setData('homeY', home.y * TILE + TILE / 2);
      sprite.setData('homeWpId', homeWpId);
      sprite.setData('spriteKey', spriteKey);
      sprite.setData('patientIdx', i);
      sprite.setData('currentWpId', isHosp ? 'hosp' : homeWpId);

      this.sprites[p.patient_id] = sprite;
      this.nameLabels[p.patient_id] = label;
      this.statusDots[p.patient_id] = dot;
    });

    // Hospital label
    const hx = this.hospitalPos.x * TILE;
    const hy = this.hospitalPos.y * TILE - 24;
    scene.add.text(hx, hy, 'HOSPITAL', {
      font: 'bold 7px monospace', fill: '#39d2c0',
      stroke: '#000000', strokeThickness: 2,
    }).setOrigin(0.5, 1).setDepth(30);

    // HUD
    this.dayText = scene.add.text(8, 8, 'DAY 1', {
      font: 'bold 14px "JetBrains Mono", monospace',
      fill: '#39d2c0', stroke: '#0d1117', strokeThickness: 3,
    }).setScrollFactor(0).setDepth(100);

    scene.add.text(8, 26, 'Drag: pan | Scroll: zoom | Click: select', {
      font: '8px monospace', fill: '#8b949e',
      stroke: '#0d1117', strokeThickness: 2,
    }).setScrollFactor(0).setDepth(100);
  }

  _createAnimations(scene) {
    for (let i = 1; i <= 10; i++) {
      const key = `patient_${String(i).padStart(2, '0')}`;
      const dirs = [
        ['down',  [0, 1, 2, 1]], ['left',  [3, 4, 5, 4]],
        ['right', [6, 7, 8, 7]], ['up',    [9, 10, 11, 10]],
      ];
      dirs.forEach(([dir, frames]) => {
        scene.anims.create({
          key: `${key}_${dir}`,
          frames: scene.anims.generateFrameNumbers(key, { frames }),
          frameRate: 6, repeat: -1,
        });
      });
    }
  }

  _update() {}

  _getStatusColor(p) {
    const aes = p.active_aes || [];
    if (aes.some(ae => ae.grade >= 3)) return STATUS_COLORS.severe;
    if (aes.length > 0) return STATUS_COLORS.moderate;
    return STATUS_COLORS.normal;
  }

  // ═══ Public API ═══════════════════════════════════════

  /**
   * Returns a Promise that resolves when all movement animations are done.
   * The auto-play controller should await this before advancing.
   */
  updateDay(dayNum, patients) {
    if (!this._scene) return Promise.resolve();
    this.currentDay = dayNum;
    if (this.dayText) this.dayText.setText(`DAY ${dayNum}`);

    let maxAnimDuration = 0;

    patients.forEach((p, i) => {
      const sprite = this.sprites[p.patient_id];
      if (!sprite) return;

      const idx = sprite.getData('patientIdx');
      const spriteKey = sprite.getData('spriteKey');
      const home = this._getHomePos(idx);
      const homeWpId = sprite.getData('homeWpId');
      const currentWpId = sprite.getData('currentWpId');

      const loc = (p.location || '').toUpperCase();
      const isHosp = loc === 'HOSPITAL' || loc === 'OUTPATIENT' || loc === 'INPATIENT';
      const targetWpId = isHosp ? 'hosp' : homeWpId;

      const tx = isHosp
        ? this.hospitalPos.x * TILE + TILE / 2 + (i % 5 - 2) * 12
        : home.x * TILE + TILE / 2;
      const ty = isHosp
        ? this.hospitalPos.y * TILE + TILE / 2 + Math.floor(i / 5) * 12
        : home.y * TILE + TILE / 2;

      const dx = tx - sprite.x;
      const dy = ty - sprite.y;
      const dist = Math.sqrt(dx * dx + dy * dy);

      if (dist > 2) {
        const path = (currentWpId && targetWpId)
          ? this.findPath(currentWpId, targetWpId) : [];

        let animDur = 0;
        if (path.length >= 2) {
          animDur = this._walkAlongPath(sprite, p.patient_id, path, tx, ty, spriteKey);
        } else {
          animDur = this._directTween(sprite, p.patient_id, tx, ty, spriteKey, dist);
        }
        if (animDur > maxAnimDuration) maxAnimDuration = animDur;
        sprite.setData('currentWpId', targetWpId);
      }

      const dot = this.statusDots[p.patient_id];
      if (dot) dot.setFillStyle(this._getStatusColor(p));
    });

    // Return a promise that resolves when all animations finish
    this._animating = maxAnimDuration > 0;
    if (this._animating) {
      return new Promise(resolve => {
        this._scene.time.delayedCall(maxAnimDuration + 100, () => {
          this._animating = false;
          resolve();
        });
      });
    }
    return Promise.resolve();
  }

  /** Returns total animation duration in ms */
  _walkAlongPath(sprite, pid, path, finalX, finalY, spriteKey) {
    const points = path.slice(1).map(wp => ({ x: wp.x, y: wp.y }));
    if (points.length > 0) {
      points[points.length - 1] = { x: finalX, y: finalY };
    }

    let totalDelay = 0;
    let prevX = sprite.x, prevY = sprite.y;
    const SPEED = 4;  // pixels per ms (faster for auto mode)

    points.forEach((pt, i) => {
      const segDx = pt.x - prevX;
      const segDy = pt.y - prevY;
      const segDist = Math.sqrt(segDx * segDx + segDy * segDy);
      const dur = Math.max(Math.min(segDist * SPEED, 800), 80);

      let dir;
      if (Math.abs(segDx) > Math.abs(segDy)) {
        dir = segDx > 0 ? 'right' : 'left';
      } else {
        dir = segDy > 0 ? 'down' : 'up';
      }

      const isLast = i === points.length - 1;

      this._scene.time.delayedCall(totalDelay, () => {
        sprite.play(`${spriteKey}_${dir}`, true);
        this._scene.tweens.add({
          targets: sprite, x: pt.x, y: pt.y,
          duration: dur, ease: 'Linear',
          onComplete: () => {
            if (isLast) {
              sprite.stop();
              const idleFrame = { down: 1, left: 4, right: 7, up: 10 };
              sprite.setFrame(idleFrame[dir]);
            }
          },
        });

        const label = this.nameLabels[pid];
        const dot = this.statusDots[pid];
        if (label) this._scene.tweens.add({ targets: label, x: pt.x, y: pt.y - 12, duration: dur, ease: 'Linear' });
        if (dot) this._scene.tweens.add({ targets: dot, x: pt.x + 6, y: pt.y - 6, duration: dur, ease: 'Linear' });
      });

      totalDelay += dur;
      prevX = pt.x;
      prevY = pt.y;
    });

    return totalDelay;
  }

  /** Returns animation duration in ms */
  _directTween(sprite, pid, tx, ty, spriteKey, dist) {
    const dx = tx - sprite.x;
    const dy = ty - sprite.y;
    let dir;
    if (Math.abs(dx) > Math.abs(dy)) {
      dir = dx > 0 ? 'right' : 'left';
    } else {
      dir = dy > 0 ? 'down' : 'up';
    }
    sprite.play(`${spriteKey}_${dir}`, true);
    const dur = Math.max(Math.min(dist * 5, 1200), 100);
    this._scene.tweens.add({
      targets: sprite, x: tx, y: ty, duration: dur, ease: 'Power2',
      onComplete: () => {
        sprite.stop();
        const idleFrame = { down: 1, left: 4, right: 7, up: 10 };
        sprite.setFrame(idleFrame[dir]);
      },
    });
    const label = this.nameLabels[pid];
    const dot = this.statusDots[pid];
    if (label) this._scene.tweens.add({ targets: label, x: tx, y: ty - 12, duration: dur, ease: 'Power2' });
    if (dot) this._scene.tweens.add({ targets: dot, x: tx + 6, y: ty - 6, duration: dur, ease: 'Power2' });
    return dur;
  }

  /** true if characters are still moving */
  isAnimating() { return this._animating; }

  highlightPatient(patientId) {
    if (!this._scene) return;
    Object.values(this.sprites).forEach(s =>
      this._scene.tweens.add({ targets: s, scale: 0.5, duration: 150 })
    );
    const sprite = this.sprites[patientId];
    if (sprite) {
      this._scene.tweens.add({ targets: sprite, scale: 0.8, duration: 150 });
      this._scene.cameras.main.pan(sprite.x, sprite.y, 400, 'Power2');
    }
  }

  focusHospital() {
    if (!this._scene) return;
    const cam = this._scene.cameras.main;
    cam.pan(this.hospitalPos.x * TILE, this.hospitalPos.y * TILE, 400, 'Power2');
    cam.zoomTo(3, 400);
  }

  setZoom(zoom) {
    if (!this._scene) return;
    this._scene.cameras.main.zoomTo(Phaser.Math.Clamp(zoom, 0.5, 5.0), 300);
  }

  fitMap() {
    if (!this._scene) return;
    const cam = this._scene.cameras.main;
    const child = this._scene.children.list.find(c => c.tilemap);
    if (child && child.tilemap) {
      const m = child.tilemap;
      const zx = this.game.config.width / m.widthInPixels;
      const zy = this.game.config.height / m.heightInPixels;
      cam.zoomTo(Math.max(Math.min(zx, zy) * 0.95, 1.0), 400);
      cam.pan(m.widthInPixels / 2, m.heightInPixels / 2, 400, 'Power2');
    }
  }

  destroy() {
    if (this.game) this.game.destroy(true);
  }

  // ═══ Speech Bubbles ═════════════════════════════════════

  showSpeechBubble(patientId, text, duration = 4000) {
    if (!this._scene) return;
    this.clearSpeechBubble(patientId);
    const sprite = this.sprites[patientId];
    if (!sprite) return;

    const bx = sprite.x, by = sprite.y - 20;
    const displayText = text.length > 24 ? text.substring(0, 22) + '...' : text;
    const tempText = this._scene.add.text(0, 0, displayText, { font: '5px monospace', fill: '#fff' });
    const tw = tempText.width, th = tempText.height;
    tempText.destroy();
    const padding = 4;
    const bgWidth = tw + padding * 2, bgHeight = th + padding * 2;

    const bg = this._scene.add.graphics();
    bg.fillStyle(0x1b1f23, 0.92);
    bg.lineStyle(1, 0x39d2c0, 0.7);
    bg.fillRoundedRect(bx - bgWidth / 2, by - bgHeight - 4, bgWidth, bgHeight, 3);
    bg.strokeRoundedRect(bx - bgWidth / 2, by - bgHeight - 4, bgWidth, bgHeight, 3);
    bg.fillStyle(0x1b1f23, 0.92);
    bg.fillTriangle(bx - 3, by - 4, bx + 3, by - 4, bx, by);
    bg.setDepth(50);

    const label = this._scene.add.text(bx, by - bgHeight / 2 - 4, displayText, {
      font: '5px monospace', fill: '#e6edf3', align: 'center',
    });
    label.setOrigin(0.5, 0.5).setDepth(51);

    bg.setAlpha(0); label.setAlpha(0);
    this._scene.tweens.add({ targets: [bg, label], alpha: 1, duration: 200 });

    this.speechBubbles[patientId] = { bg, label };
    this.bubbleTimers[patientId] = this._scene.time.delayedCall(duration, () => {
      this.clearSpeechBubble(patientId);
    });
  }

  clearSpeechBubble(patientId) {
    const bubble = this.speechBubbles[patientId];
    if (bubble) {
      if (bubble.bg) bubble.bg.destroy();
      if (bubble.label) bubble.label.destroy();
      delete this.speechBubbles[patientId];
    }
    if (this.bubbleTimers[patientId]) {
      this.bubbleTimers[patientId].remove();
      delete this.bubbleTimers[patientId];
    }
  }

  clearAllBubbles() {
    Object.keys(this.speechBubbles).forEach(pid => this.clearSpeechBubble(pid));
  }

  updateBubbles(patients) {
    this.clearAllBubbles();
    const alertPatients = [];

    patients.forEach((p, i) => {
      const awareness = (p.awareness || 'UNAWARE').toUpperCase();
      if (awareness !== 'UNAWARE' && awareness !== '?') {
        const messages = AWARENESS_MESSAGES[awareness];
        if (messages) {
          let text = null;
          if (p.symptoms_perceived && p.symptoms_perceived.length > 0) {
            const sym = p.symptoms_perceived[0];
            if (typeof sym === 'object' && sym.verbal_expression) {
              text = sym.verbal_expression;
            }
          }
          if (!text) text = messages[Math.floor(Math.random() * messages.length)];
          const delay = i * 300 + Math.random() * 500;
          if (this._scene) {
            this._scene.time.delayedCall(delay, () => {
              this.showSpeechBubble(p.patient_id, text,
                awareness === 'EMERGENCY' ? 8000 :
                awareness === 'DISTRESSED' ? 6000 : 4000);
            });
          }
        }
      }

      if (p.care_record && p.care_record.severity_level) {
        const sev = p.care_record.severity_level.toLowerCase();
        if (sev === 'red' || sev === 'orange') {
          alertPatients.push({
            patient_id: p.patient_id, severity: sev,
            summary: p.care_record.summary || 'Needs attention',
            actions: p.care_record.actions || [],
          });
        }
      }
    });

    if (alertPatients.length > 0 && this.onPhoneCall) {
      alertPatients.sort((a, b) => {
        const order = { red: 0, orange: 1 };
        return (order[a.severity] || 2) - (order[b.severity] || 2);
      });
      this.onPhoneCall(alertPatients);
    }
  }
}
