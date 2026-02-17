/**
 * Clinical Trial Map — Kenney Tiny Town tileset
 * 16x16 tiles rendered at 2x zoom for crisp pixel art.
 * 
 * Layout:
 *   Top:      5 patient homes
 *   Middle:   Hospital (left) + stone roads
 *   Bottom:   5 patient homes
 */

const TILE = 16;
const STATIC = '/static/assets';
const RENDER_SCALE = 2;  // 2x zoom for 16px tiles

// Positions from build_kenney_map.py (42x30 map)
const HOSPITAL_POS = { x: 4, y: 14 };

const HOME_POSITIONS = [
  { x: 17, y: 6 },   // PT-001
  { x: 22, y: 6 },   // PT-002
  { x: 27, y: 6 },   // PT-003
  { x: 32, y: 6 },   // PT-004
  { x: 37, y: 6 },   // PT-005
  { x: 17, y: 26 },  // PT-006
  { x: 22, y: 26 },  // PT-007
  { x: 27, y: 26 },  // PT-008
  { x: 32, y: 26 },  // PT-009
  { x: 37, y: 26 },  // PT-010
];

const STATUS_COLORS = {
  severe:   0xf85149,
  moderate: 0xd29922,
  normal:   0x3fb950,
};

class TrialMap {
  constructor(containerId, patients, onPatientClick) {
    this.patients = patients || [];
    this.onPatientClick = onPatientClick;
    this.sprites = {};
    this.nameLabels = {};
    this.statusDots = {};
    this._scene = null;

    const el = document.getElementById(containerId);
    const w = el ? el.clientWidth : 1200;

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
  }

  // ─── Preload ─────────────────────────────────────────
  _preload() {
    const s = this.game.scene.scenes[0];

    // Kenney Tiny Town tileset (packed spritesheet with 1px spacing)
    s.load.tilemapTiledJSON('map', `${STATIC}/map/clinical_trial.json`);
    s.load.image('tiny-town', `${STATIC}/kenney/tiny-town/Tilemap/tilemap_packed.png`);

    // Character sprites (96x128 = 3x4 grid of 32x32)
    for (let i = 1; i <= 10; i++) {
      const key = `patient_${String(i).padStart(2, '0')}`;
      s.load.spritesheet(key, `${STATIC}/characters/${key}.png`, {
        frameWidth: 32,
        frameHeight: 32,
      });
    }
  }

  // ─── Create ──────────────────────────────────────────
  _create() {
    this._scene = this.game.scene.scenes[0];
    const scene = this._scene;

    // ── Tilemap ──
    const map = scene.make.tilemap({ key: 'map' });
    // tilemap_packed.png: no margin, no spacing (packed format)
    const tileset = map.addTilesetImage('tiny-town', 'tiny-town', 16, 16, 0, 0);
    
    if (!tileset) {
      console.error('Failed to load tileset');
      return;
    }

    // Create layers
    const layerNames = ['Ground', 'Buildings', 'Roofs', 'Decor'];
    const layers = {};
    layerNames.forEach((name, i) => {
      try {
        const layer = map.createLayer(name, tileset, 0, 0);
        if (layer) {
          layers[name] = layer;
          layer.setDepth(i);
        }
      } catch (e) {
        console.warn(`Layer "${name}":`, e.message);
      }
    });

    // ── Camera ──
    const camera = scene.cameras.main;
    camera.setBounds(0, 0, map.widthInPixels, map.heightInPixels);
    
    // Zoom to fit map nicely
    const zoomX = this.game.config.width / map.widthInPixels;
    const zoomY = this.game.config.height / map.heightInPixels;
    const fitZoom = Math.min(zoomX, zoomY) * 0.95;
    camera.setZoom(Math.max(fitZoom, RENDER_SCALE));
    camera.centerOn(map.widthInPixels / 2, map.heightInPixels / 2);

    // Drag to pan
    scene.input.on('pointermove', (pointer) => {
      if (pointer.isDown) {
        camera.scrollX -= (pointer.x - pointer.prevPosition.x) / camera.zoom;
        camera.scrollY -= (pointer.y - pointer.prevPosition.y) / camera.zoom;
      }
    });
    // Scroll to zoom
    scene.input.on('wheel', (p, go, dx, dy) => {
      camera.setZoom(Phaser.Math.Clamp(camera.zoom - dy * 0.003, 1.0, 5.0));
    });

    // ── Character animations ──
    this._createAnimations(scene);

    // ── Place patients ──
    this.patients.forEach((p, i) => {
      const idx = i % 10;
      const spriteKey = `patient_${String(idx + 1).padStart(2, '0')}`;
      const home = HOME_POSITIONS[idx] || HOME_POSITIONS[0];

      const loc = (p.location || '').toUpperCase();
      const isHosp = loc === 'HOSPITAL' || loc === 'OUTPATIENT' || loc === 'INPATIENT';
      const pos = isHosp ? HOSPITAL_POS : home;
      const px = pos.x * TILE + TILE / 2 + (isHosp ? (i % 5 - 2) * 12 : 0);
      const py = pos.y * TILE + TILE / 2 + (isHosp ? Math.floor(i / 5) * 12 : 0);

      // Sprite (scale down to match 16px tiles — char is 32px, tile is 16px)
      const sprite = scene.add.sprite(px, py, spriteKey, 1);
      sprite.setScale(0.5);  // 32→16px to match tile size
      sprite.setDepth(10);
      sprite.setInteractive({ useHandCursor: true });
      sprite.on('pointerdown', () => {
        if (this.onPatientClick) this.onPatientClick(p.patient_id);
        this.highlightPatient(p.patient_id);
      });

      // Name label
      const label = scene.add.text(px, py - 12, p.patient_id, {
        font: 'bold 5px monospace',
        fill: '#ffffff',
        stroke: '#000000',
        strokeThickness: 2,
        align: 'center',
      });
      label.setOrigin(0.5, 1);
      label.setDepth(20);

      // Status dot
      const color = this._getStatusColor(p);
      const dot = scene.add.circle(px + 6, py - 6, 2, color);
      dot.setDepth(21);
      dot.setStrokeStyle(1, 0x000000);

      sprite.setData('homeX', home.x * TILE + TILE / 2);
      sprite.setData('homeY', home.y * TILE + TILE / 2);
      sprite.setData('spriteKey', spriteKey);
      sprite.setData('patientIdx', idx);

      this.sprites[p.patient_id] = sprite;
      this.nameLabels[p.patient_id] = label;
      this.statusDots[p.patient_id] = dot;
    });

    // ── Hospital label ──
    scene.add.text(
      HOSPITAL_POS.x * TILE, HOSPITAL_POS.y * TILE - 24,
      '🏥 HOSPITAL', {
        font: 'bold 7px monospace',
        fill: '#39d2c0',
        stroke: '#000000',
        strokeThickness: 2,
      }
    ).setOrigin(0.5, 1).setDepth(30);

    // ── Home labels ──
    this.patients.forEach((p, i) => {
      const idx = i % 10;
      const home = HOME_POSITIONS[idx];
      scene.add.text(
        home.x * TILE, home.y * TILE - 24,
        p.patient_id, {
          font: 'bold 5px monospace',
          fill: '#bc8cff',
          stroke: '#000000',
          strokeThickness: 2,
        }
      ).setOrigin(0.5, 1).setDepth(30);
    });

    // ── HUD (fixed to camera) ──
    this.dayText = scene.add.text(8, 8, 'DAY 1', {
      font: 'bold 14px "JetBrains Mono", monospace',
      fill: '#39d2c0',
      stroke: '#0d1117',
      strokeThickness: 3,
    }).setScrollFactor(0).setDepth(100);

    scene.add.text(8, 26, 'Drag: pan · Scroll: zoom · Click: select', {
      font: '8px monospace',
      fill: '#8b949e',
      stroke: '#0d1117',
      strokeThickness: 2,
    }).setScrollFactor(0).setDepth(100);
  }

  _createAnimations(scene) {
    // RPG Maker layout: 3 cols x 4 rows
    // Down: 0,1,2  Left: 3,4,5  Right: 6,7,8  Up: 9,10,11
    for (let i = 1; i <= 10; i++) {
      const key = `patient_${String(i).padStart(2, '0')}`;
      const dirs = [
        ['down',  [0, 1, 2, 1]],
        ['left',  [3, 4, 5, 4]],
        ['right', [6, 7, 8, 7]],
        ['up',    [9, 10, 11, 10]],
      ];
      dirs.forEach(([dir, frames]) => {
        scene.anims.create({
          key: `${key}_${dir}`,
          frames: scene.anims.generateFrameNumbers(key, { frames }),
          frameRate: 6,
          repeat: -1,
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

  updateDay(dayNum, patients) {
    if (!this._scene) return;
    this.currentDay = dayNum;
    if (this.dayText) this.dayText.setText(`DAY ${dayNum}`);

    patients.forEach((p, i) => {
      const sprite = this.sprites[p.patient_id];
      if (!sprite) return;

      const idx = sprite.getData('patientIdx');
      const spriteKey = sprite.getData('spriteKey');
      const home = HOME_POSITIONS[idx];

      const loc = (p.location || '').toUpperCase();
      const isHosp = loc === 'HOSPITAL' || loc === 'OUTPATIENT' || loc === 'INPATIENT';
      const tx = isHosp
        ? HOSPITAL_POS.x * TILE + TILE/2 + (i % 5 - 2) * 12
        : home.x * TILE + TILE/2;
      const ty = isHosp
        ? HOSPITAL_POS.y * TILE + TILE/2 + Math.floor(i / 5) * 12
        : home.y * TILE + TILE/2;

      const dx = tx - sprite.x;
      const dy = ty - sprite.y;
      const dist = Math.sqrt(dx*dx + dy*dy);

      if (dist > 2) {
        // Walk animation
        let dir;
        if (Math.abs(dx) > Math.abs(dy)) {
          dir = dx > 0 ? 'right' : 'left';
        } else {
          dir = dy > 0 ? 'down' : 'up';
        }
        sprite.play(`${spriteKey}_${dir}`, true);

        const dur = Math.min(dist * 8, 1500);
        this._scene.tweens.add({
          targets: sprite,
          x: tx, y: ty,
          duration: dur,
          ease: 'Power2',
          onComplete: () => {
            sprite.stop();
            const idleFrame = { down: 1, left: 4, right: 7, up: 10 };
            sprite.setFrame(idleFrame[dir]);
          }
        });

        // Move label and dot
        const label = this.nameLabels[p.patient_id];
        const dot = this.statusDots[p.patient_id];
        if (label) this._scene.tweens.add({ targets: label, x: tx, y: ty - 12, duration: dur, ease: 'Power2' });
        if (dot)   this._scene.tweens.add({ targets: dot,   x: tx + 6, y: ty - 6, duration: dur, ease: 'Power2' });
      }

      // Update status
      const dot = this.statusDots[p.patient_id];
      if (dot) dot.setFillStyle(this._getStatusColor(p));
    });
  }

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
    cam.pan(HOSPITAL_POS.x * TILE, HOSPITAL_POS.y * TILE, 400, 'Power2');
    cam.zoomTo(3, 400);
  }

  setZoom(zoom) {
    if (!this._scene) return;
    this._scene.cameras.main.zoomTo(Phaser.Math.Clamp(zoom, 1.0, 5.0), 300);
  }

  fitMap() {
    if (!this._scene) return;
    const cam = this._scene.cameras.main;
    const child = this._scene.children.list.find(c => c.tilemap);
    if (child && child.tilemap) {
      const m = child.tilemap;
      const zx = this.game.config.width / m.widthInPixels;
      const zy = this.game.config.height / m.heightInPixels;
      cam.zoomTo(Math.max(Math.min(zx, zy) * 0.95, RENDER_SCALE), 400);
      cam.pan(m.widthInPixels / 2, m.heightInPixels / 2, 400, 'Power2');
    }
  }

  destroy() {
    if (this.game) this.game.destroy(true);
  }
}