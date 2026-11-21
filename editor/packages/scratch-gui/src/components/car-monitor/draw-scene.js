import {
    COLORS,
    CONE_HALF_ANGLE_DEG,
    brightestIndex,
    coneStyle,
    glowRadius,
    gridPositions,
    headingToCanvasAngle,
    lightSensorOffsets,
    lineSensorOffsets,
    treadColor,
    treadDirection
} from './car-renderer.js';

const DEG = Math.PI / 180;
const GRID_STEP_CM = 10;
const STRIPE_SPACING_CM = 1;
const WHEEL_LENGTH_CM = 6;
const WHEEL_WIDTH_CM = 2.4;

const drawGrid = (ctx, view, width, height) => {
    const halfW = width / 2 / view.scale;
    const midY = (height / 2) + (view.oy || 0);
    const halfH = height / 2 / view.scale;
    ctx.lineWidth = 1;
    ctx.strokeStyle = 'rgba(96,125,139,0.22)';
    ctx.beginPath();
    for (const x of gridPositions(view.cx - halfW, view.cx + halfW, GRID_STEP_CM)) {
        const sx = Math.round((width / 2) + ((x - view.cx) * view.scale)) + 0.5;
        ctx.moveTo(sx, 0);
        ctx.lineTo(sx, height);
    }
    for (const y of gridPositions(view.cy - halfH, view.cy + halfH, GRID_STEP_CM)) {
        const sy = Math.round(midY - ((y - view.cy) * view.scale)) + 0.5;
        ctx.moveTo(0, sy);
        ctx.lineTo(width, sy);
    }
    ctx.stroke();
};

const drawWorld = (ctx, world) => {
    if (!world) return;
    const [w, h] = world.size || [0, 0];
    if (w > 0 && h > 0) {
        ctx.fillStyle = 'rgba(255,255,255,0.55)';
        ctx.fillRect(-w / 2, -h / 2, w, h);
    }
    if (world.line && Array.isArray(world.line.points) && world.line.points.length > 1) {
        const pts = world.line.points;
        ctx.strokeStyle = '#263238';
        ctx.lineWidth = world.line.width || 2;
        ctx.lineJoin = 'round';
        ctx.lineCap = 'round';
        ctx.beginPath();
        ctx.moveTo(pts[0][0], pts[0][1]);
        for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
        if (world.line.closed) ctx.closePath();
        ctx.stroke();
    }
    if (Array.isArray(world.walls)) {
        ctx.strokeStyle = '#37474F';
        ctx.lineWidth = 2.5;
        ctx.lineCap = 'round';
        ctx.beginPath();
        for (const wall of world.walls) {
            ctx.moveTo(wall[0], wall[1]);
            ctx.lineTo(wall[2], wall[3]);
        }
        ctx.stroke();
    }
    if (Array.isArray(world.lights)) {
        for (const light of world.lights) {
            const r = 4 + (6 * (light.intensity || 1));
            const g = ctx.createRadialGradient(light.x, light.y, 0, light.x, light.y, r);
            g.addColorStop(0, 'rgba(255,235,59,0.95)');
            g.addColorStop(1, 'rgba(255,235,59,0)');
            ctx.fillStyle = g;
            ctx.beginPath();
            ctx.arc(light.x, light.y, r, 0, 2 * Math.PI);
            ctx.fill();
            ctx.fillStyle = '#FBC02D';
            ctx.beginPath();
            ctx.arc(light.x, light.y, 1.4, 0, 2 * Math.PI);
            ctx.fill();
        }
    }
};

const drawTrail = (ctx, trail, scale) => {
    if (trail.length < 2) return;
    ctx.strokeStyle = 'rgba(30,136,229,0.7)';
    ctx.lineWidth = 2 / scale;
    ctx.lineJoin = 'round';
    ctx.beginPath();
    ctx.moveTo(trail[0].x, trail[0].y);
    for (let i = 1; i < trail.length; i++) ctx.lineTo(trail[i].x, trail[i].y);
    ctx.stroke();
};

// Obstacle dots, oldest first: three alpha buckets, one path and one fill each (cheap per frame).
const OBSTACLE_ALPHAS = [0.22, 0.45, 0.75];
const drawObstacles = (ctx, points, scale) => {
    const n = points.length;
    if (!n) return;
    const side = Math.max(1.2, 3 / scale);
    const half = side / 2;
    ctx.fillStyle = '#263238';
    for (let b = 0; b < OBSTACLE_ALPHAS.length; b++) {
        const from = Math.floor((n * b) / OBSTACLE_ALPHAS.length);
        const to = Math.floor((n * (b + 1)) / OBSTACLE_ALPHAS.length);
        if (to <= from) continue;
        ctx.globalAlpha = OBSTACLE_ALPHAS[b];
        ctx.beginPath();
        for (let i = from; i < to; i++) ctx.rect(points[i].x - half, points[i].y - half, side, side);
        ctx.fill();
    }
    ctx.globalAlpha = 1;
};

let hatchPattern = null;
const getHatchPattern = ctx => {
    if (hatchPattern || typeof document === 'undefined') return hatchPattern;
    const c = document.createElement('canvas');
    c.width = 8;
    c.height = 8;
    const p = c.getContext('2d');
    if (!p) return null;
    p.strokeStyle = 'rgba(96,125,139,0.9)';
    p.lineWidth = 1.5;
    p.beginPath();
    p.moveTo(0, 8);
    p.lineTo(8, 0);
    p.stroke();
    hatchPattern = ctx.createPattern(c, 'repeat');
    return hatchPattern;
};

const drawCone = (ctx, geometry, tel, scale) => {
    const style = coneStyle(tel && tel.dist, tel && tel.safety ? tel.safety.margin_cm : 0);
    const a = CONE_HALF_ANGLE_DEG * DEG;
    ctx.save();
    ctx.translate(geometry.ultrasonic_x, 0);
    ctx.beginPath();
    ctx.moveTo(0, 0);
    ctx.arc(0, 0, style.length, -a, a);
    ctx.closePath();
    if (style.kind === 'unknown') {
        ctx.fillStyle = getHatchPattern(ctx) || 'rgba(96,125,139,0.3)';
        ctx.globalAlpha = 0.6;
        ctx.fill();
        ctx.globalAlpha = 1;
        ctx.strokeStyle = 'rgba(96,125,139,0.9)';
        ctx.setLineDash([3 / scale, 3 / scale]);
        ctx.lineWidth = 1 / scale;
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = '#455A64';
        ctx.font = `bold ${16 / scale}px sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText('?', style.length * 0.6, 0);
    } else {
        ctx.globalAlpha = style.alpha;
        ctx.fillStyle = style.color;
        ctx.fill();
        ctx.globalAlpha = 1;
        ctx.strokeStyle = style.color;
        ctx.lineWidth = 1.5 / scale;
        ctx.stroke();
    }
    ctx.restore();
};

const drawWheel = (ctx, x, y, angleDeg, tread) => {
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(angleDeg * DEG);
    ctx.fillStyle = '#263238';
    ctx.fillRect(-WHEEL_LENGTH_CM / 2, -WHEEL_WIDTH_CM / 2, WHEEL_LENGTH_CM, WHEEL_WIDTH_CM);
    if (tread) {
        ctx.save();
        ctx.beginPath();
        ctx.rect(-WHEEL_LENGTH_CM / 2, -WHEEL_WIDTH_CM / 2, WHEEL_LENGTH_CM, WHEEL_WIDTH_CM);
        ctx.clip();
        ctx.strokeStyle = tread.color;
        ctx.lineWidth = 0.4;
        ctx.beginPath();
        for (let sx = (-WHEEL_LENGTH_CM / 2) - tread.phase; sx < WHEEL_LENGTH_CM / 2; sx += STRIPE_SPACING_CM) {
            ctx.moveTo(sx, -WHEEL_WIDTH_CM / 2);
            ctx.lineTo(sx, WHEEL_WIDTH_CM / 2);
        }
        ctx.stroke();
        ctx.restore();
    }
    ctx.restore();
};

const drawCar = (ctx, geometry, tel, treadPhase) => {
    const {body, wheelbase, track} = geometry;
    const direction = treadDirection(tel && tel.drive);
    const tread = {color: treadColor(direction), phase: treadPhase};
    const steer = tel && typeof tel.steer === 'number' ? tel.steer : 0;

    ctx.fillStyle = '#E53935';
    ctx.strokeStyle = '#8E1B1B';
    ctx.lineWidth = 0.5;
    const x0 = -body.rear_overhang;
    ctx.beginPath();
    if (ctx.roundRect) {
        ctx.roundRect(x0, -body.width / 2, body.length, body.width, 2.5);
    } else {
        ctx.rect(x0, -body.width / 2, body.length, body.width);
    }
    ctx.fill();
    ctx.stroke();

    // Windscreen strip marks the front.
    ctx.fillStyle = 'rgba(236,239,241,0.85)';
    ctx.fillRect(wheelbase + 1, (-body.width / 2) + 2, 3, body.width - 4);

    drawWheel(ctx, 0, -track / 2, 0, tread);
    drawWheel(ctx, 0, track / 2, 0, tread);
    drawWheel(ctx, wheelbase, -track / 2, steer, null);
    drawWheel(ctx, wheelbase, track / 2, steer, null);

    // Ultrasonic eyes.
    ctx.fillStyle = '#ECEFF1';
    ctx.strokeStyle = '#263238';
    ctx.lineWidth = 0.4;
    for (const side of [-1, 1]) {
        ctx.beginPath();
        ctx.arc(geometry.ultrasonic_x, side * 3, 1.5, 0, 2 * Math.PI);
        ctx.fill();
        ctx.stroke();
    }
};

const drawLineSensors = (ctx, geometry, tel) => {
    const offsets = lineSensorOffsets(geometry);
    const line = tel && tel.line;
    const ok = Boolean(line && line.ok);
    offsets.forEach((o, i) => {
        ctx.beginPath();
        ctx.arc(o.forward, o.right, 0.75, 0, 2 * Math.PI);
        if (!ok) {
            ctx.strokeStyle = COLORS.red;
            ctx.lineWidth = 0.35;
            ctx.beginPath();
            const r = 0.7;
            ctx.moveTo(o.forward - r, o.right - r);
            ctx.lineTo(o.forward + r, o.right + r);
            ctx.moveTo(o.forward - r, o.right + r);
            ctx.lineTo(o.forward + r, o.right - r);
            ctx.stroke();
            return;
        }
        const on = Array.isArray(line.on) && line.on[i];
        ctx.fillStyle = on ? '#212121' : '#ECEFF1';
        ctx.fill();
        ctx.strokeStyle = '#263238';
        ctx.lineWidth = 0.3;
        ctx.stroke();
    });
};

const drawLights = (ctx, geometry, tel) => {
    const light = tel && tel.light;
    if (!light || !light.ok || !Array.isArray(light.bright)) return;
    lightSensorOffsets(geometry).forEach((o, i) => {
        const bright = light.bright[i] || 0;
        const r = glowRadius(bright);
        const g = ctx.createRadialGradient(o.forward, o.right, 0, o.forward, o.right, r);
        g.addColorStop(0, `rgba(255,214,0,${(0.35 + (0.6 * bright / 100)).toFixed(2)})`);
        g.addColorStop(1, 'rgba(255,214,0,0)');
        ctx.fillStyle = g;
        ctx.beginPath();
        ctx.arc(o.forward, o.right, r, 0, 2 * Math.PI);
        ctx.fill();
    });
    const best = brightestIndex(light.max);
    if (best >= 0) {
        const o = lightSensorOffsets(geometry)[best];
        const angle = (best - 1) * 35 * DEG;
        ctx.save();
        ctx.translate(o.forward + 2, o.right * 1.5);
        ctx.rotate(angle);
        ctx.strokeStyle = '#F9A825';
        ctx.fillStyle = '#F9A825';
        ctx.lineWidth = 0.9;
        ctx.beginPath();
        ctx.moveTo(0, 0);
        ctx.lineTo(9, 0);
        ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(11, 0);
        ctx.lineTo(7.5, -2);
        ctx.lineTo(7.5, 2);
        ctx.closePath();
        ctx.fill();
        ctx.restore();
    }
};

const HUD_HEIGHT = 22;

const hudText = tel => {
    if (!tel) return '—';
    const drive = tel.drive || {};
    const dir = drive.dir ? drive.dir : 'stopped';
    const speed = drive.dir ? `${Math.round(drive.speed || 0)}%` : '0%';
    const steer = `${Math.round(typeof tel.steer === 'number' ? tel.steer : 0)}°`.replace('-', '−');
    let dist = '—';
    if (tel.dist) {
        if (tel.dist.state === 'ok' && typeof tel.dist.cm === 'number') dist = `${tel.dist.cm.toFixed(0)} cm`;
        else if (tel.dist.state === 'far') dist = 'far';
        else dist = '?';
    }
    return `${speed} · ${dir} · ${steer} · ${dist}`;
};

// One strip along the bottom edge of the canvas (computeView reserves its height).
const drawHud = (ctx, tel, width, height) => {
    ctx.font = 'bold 13px sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = 'rgba(255,255,255,0.88)';
    ctx.fillRect(0, height - HUD_HEIGHT, width, HUD_HEIGHT);
    ctx.fillStyle = '#263238';
    ctx.fillText(hudText(tel), width / 2, height - (HUD_HEIGHT / 2), width - 8);
};

/**
 * Draw one frame of the monitor.
 * @param {CanvasRenderingContext2D} ctx - context, already scaled for devicePixelRatio
 * @param {object} scene - everything to draw
 * @param {number} scene.width - canvas CSS width
 * @param {number} scene.height - canvas CSS height
 * @param {{cx: number, cy: number, scale: number}} scene.view - camera
 * @param {?object} scene.world - sim world or null
 * @param {object} scene.geometry - normalised car geometry
 * @param {{x: number, y: number, dir: number}} scene.pose - pose to draw the car at
 * @param {?object} scene.tel - latest telemetry
 * @param {Array<{x: number, y: number}>} scene.trail - trail points
 * @param {?Array<{x: number, y: number}>} scene.obstacles - obstacle map points, oldest first
 * @param {number} scene.treadPhase - tread stripe phase in cm
 * @param {boolean} scene.showLine - draw the line sensor dots
 */
const drawScene = (ctx, scene) => {
    const {width, height, view, world, geometry, pose, tel, trail, treadPhase, showLine} = scene;
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = '#ECEFF1';
    ctx.fillRect(0, 0, width, height);

    drawGrid(ctx, view, width, height);

    ctx.save();
    // World frame: cm, +y up.
    ctx.translate(width / 2, (height / 2) + (view.oy || 0));
    ctx.scale(view.scale, -view.scale);
    ctx.translate(-view.cx, -view.cy);
    drawWorld(ctx, world);
    drawTrail(ctx, trail, view.scale);
    if (scene.obstacles) drawObstacles(ctx, scene.obstacles, view.scale);

    // Car frame: +x forward, +y right. The world frame is y-up, so flip back before rotating.
    ctx.translate(pose.x, pose.y);
    ctx.scale(1, -1);
    ctx.rotate(headingToCanvasAngle(pose.dir));
    drawCone(ctx, geometry, tel, view.scale);
    drawCar(ctx, geometry, tel, treadPhase);
    if (showLine) drawLineSensors(ctx, geometry, tel);
    drawLights(ctx, geometry, tel);
    ctx.restore();

    drawHud(ctx, tel, width, height);
};

export {drawScene, hudText, HUD_HEIGHT};
