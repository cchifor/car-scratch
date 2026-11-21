import PropTypes from 'prop-types';
import React, {useEffect, useRef} from 'react';
import {useIntl} from 'react-intl';

import {
    advanceTreadPhase,
    computeView,
    interpolatePose,
    normalizeGeometry,
    pushTrail,
    treadDirection
} from './car-renderer.js';
import {drawScene} from './draw-scene.js';
import {ObstacleMap, computeRoomView, growBounds, isPoseReset, newBounds} from './room-map.js';
import messages from './messages.js';
import styles from './car-monitor.css';

const START_POSE = {x: 0, y: 0, dir: 90};
const FRAME_MIN_MS = 20;
const FRAME_MAX_MS = 120;

const poseOf = tel => {
    const p = tel && tel.pose;
    if (!p || typeof p.x !== 'number' || typeof p.y !== 'number') return null;
    return {x: p.x, y: p.y, dir: typeof p.dir === 'number' ? p.dir : 90};
};

const now = () => (typeof performance === 'undefined' ? Date.now() : performance.now());

/**
 * The top-down drawing. Telemetry goes straight into a mutable scene held in a ref and is painted
 * by a requestAnimationFrame loop; React never re-renders per telemetry frame.
 * @param {object} props - props
 * @param {object} props.link - the CarLink
 * @param {?object} props.geometry - welcome.geometry
 * @param {?object} props.world - sim world to draw (sim mode only)
 * @param {boolean} props.showLine - draw the line sensor dots
 * @param {string} props.resetKey - changes whenever the trail must restart (mode switch, reconnect, world)
 * @param {string} [props.viewMode] - 'room' (fixed camera, default) or 'follow' (camera tracks the car)
 * @param {number} [props.clearToken] - changes when the user pressed "Clear map"
 * @returns {React.Element} the canvas
 */
const MonitorCanvas = ({link, geometry, world, showLine, resetKey, viewMode = 'room', clearToken = 0}) => {
    const intl = useIntl();
    const wrapRef = useRef(null);
    const canvasRef = useRef(null);
    const sizeRef = useRef({width: 0, height: 0, dpr: 1});
    const inputRef = useRef({geometry: normalizeGeometry(geometry), world, showLine, viewMode});
    const sceneRef = useRef({
        tel: null,
        trail: [],
        from: null,
        to: null,
        at: 0,
        interval: 50,
        shown: START_POSE,
        cam: null,
        phase: 0,
        map: new ObstacleMap(),
        bounds: newBounds(),
        lastPose: null
    });

    useEffect(() => {
        inputRef.current = {geometry: normalizeGeometry(geometry), world, showLine, viewMode};
    }, [geometry, world, showLine, viewMode]);

    // "Clear map": forget the obstacle points and refit the room view from scratch.
    useEffect(() => {
        const scene = sceneRef.current;
        scene.map.clear();
        scene.bounds = newBounds();
        if (scene.shown) growBounds(scene.bounds, scene.shown);
    }, [clearToken]);

    // Telemetry feeds the scene directly.
    useEffect(() => {
        const scene = sceneRef.current;
        scene.tel = link.getTelemetry ? link.getTelemetry() : null;
        scene.trail.length = 0;
        const initial = poseOf(scene.tel);
        scene.from = null;
        scene.to = initial;
        scene.shown = initial || START_POSE;
        if (initial) pushTrail(scene.trail, initial);
        scene.cam = null;
        scene.map.clear();
        scene.bounds = newBounds();
        scene.lastPose = initial;
        if (initial) growBounds(scene.bounds, initial);
        const onTelemetry = tel => {
            const t = now();
            const pose = poseOf(tel);
            scene.tel = tel;
            if (!pose) return;
            scene.interval = Math.min(FRAME_MAX_MS, Math.max(FRAME_MIN_MS, t - scene.at));
            scene.at = t;
            scene.from = scene.shown;
            scene.to = pose;
            if (isPoseReset(scene.lastPose, pose)) {
                // reset_pose or a teleport: the old map and room no longer apply.
                scene.map.clear();
                scene.bounds = newBounds();
                scene.from = null;
            }
            scene.lastPose = pose;
            pushTrail(scene.trail, pose);
            growBounds(scene.bounds, pose);
            const hit = scene.map.addReading(pose, inputRef.current.geometry, tel.dist);
            if (hit) growBounds(scene.bounds, hit);
        };
        link.on('telemetry', onTelemetry);
        return () => link.removeListener('telemetry', onTelemetry);
    }, [link, resetKey]);

    // Keep the backing store matched to the container and devicePixelRatio.
    useEffect(() => {
        const wrap = wrapRef.current;
        const canvas = canvasRef.current;
        if (!wrap || !canvas) return;
        const measure = () => {
            const rect = wrap.getBoundingClientRect();
            const dpr = (typeof window !== 'undefined' && window.devicePixelRatio) || 1;
            const width = Math.floor(rect.width);
            const height = Math.floor(rect.height);
            sizeRef.current = {width, height, dpr};
            const pw = Math.max(1, Math.round(width * dpr));
            const ph = Math.max(1, Math.round(height * dpr));
            if (canvas.width !== pw) canvas.width = pw;
            if (canvas.height !== ph) canvas.height = ph;
        };
        measure();
        if (typeof ResizeObserver === 'function') {
            const observer = new ResizeObserver(measure);
            observer.observe(wrap);
            return () => observer.disconnect();
        }
        window.addEventListener('resize', measure);
        return () => window.removeEventListener('resize', measure);
    }, []);

    // Paint loop.
    useEffect(() => {
        let raf = 0;
        let last = now();
        const frame = () => {
            raf = requestAnimationFrame(frame);
            const t = now();
            const dt = Math.min(0.25, (t - last) / 1000);
            last = t;
            const canvas = canvasRef.current;
            const {width, height, dpr} = sizeRef.current;
            if (!canvas || width < 2 || height < 2) return;
            let ctx = null;
            try {
                ctx = canvas.getContext('2d');
            } catch (e) {
                ctx = null;
            }
            if (!ctx) return;

            const scene = sceneRef.current;
            const input = inputRef.current;
            if (scene.to) {
                scene.shown = interpolatePose(scene.from, scene.to, (t - scene.at) / scene.interval);
            }
            const pose = scene.shown;
            const sceneWorld = input.world;
            if (!scene.cam) scene.cam = {x: pose.x, y: pose.y};
            scene.cam = {
                x: scene.cam.x + ((pose.x - scene.cam.x) * 0.25),
                y: scene.cam.y + ((pose.y - scene.cam.y) * 0.25)
            };
            const useRoom = !sceneWorld && input.viewMode !== 'follow';
            const view = useRoom ?
                computeRoomView({width, height, bounds: scene.bounds}) :
                computeView({width, height, world: sceneWorld, pose: scene.cam});
            const tel = scene.tel;
            scene.phase = advanceTreadPhase(
                scene.phase,
                treadDirection(tel && tel.drive),
                tel && tel.drive ? tel.drive.speed : 0,
                dt
            );
            ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
            drawScene(ctx, {
                width,
                height,
                view,
                world: sceneWorld,
                geometry: input.geometry,
                pose,
                tel,
                trail: scene.trail,
                obstacles: scene.map.points,
                treadPhase: scene.phase,
                showLine: input.showLine
            });
        };
        raf = requestAnimationFrame(frame);
        return () => cancelAnimationFrame(raf);
    }, []);

    return (
        <div
            ref={wrapRef}
            className={styles.canvasWrap}
        >
            <canvas
                ref={canvasRef}
                className={styles.canvas}
                role="img"
                aria-label={intl.formatMessage(messages.canvasAria)}
            />
        </div>
    );
};

MonitorCanvas.propTypes = {
    geometry: PropTypes.shape({}),
    clearToken: PropTypes.number,
    link: PropTypes.shape({
        getTelemetry: PropTypes.func,
        on: PropTypes.func.isRequired,
        removeListener: PropTypes.func.isRequired
    }).isRequired,
    resetKey: PropTypes.string,
    showLine: PropTypes.bool,
    viewMode: PropTypes.oneOf(['room', 'follow']),
    world: PropTypes.shape({})
};

export default MonitorCanvas;
