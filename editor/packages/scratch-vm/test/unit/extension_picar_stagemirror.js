const {test} = require('tap');
const PiCar = require('../../src/extensions/scratch3_picar');
const CarLink = require('../../src/extensions/scratch3_picar/car-link');
const F = require('../fixtures/picar/fakes');

const {FakeRuntime, FakeStore, ThrowingStore, tel, makeLink, connect} = F;

class FakeSprite {
    constructor (name, x = 0, y = 0, direction = 90) {
        this.name = name;
        this.x = x;
        this.y = y;
        this.direction = direction;
        this.isStage = false;
        this.isOriginal = true;
        this.moves = 0;
        this.turns = 0;
    }

    getName () {
        return this.name;
    }

    setXY (x, y) {
        this.moves++;
        this.x = x;
        this.y = y;
    }

    setDirection (d) {
        this.turns++;
        this.direction = d;
    }
}

const setup = (opts = {}) => {
    const ctx = makeLink(CarLink, opts.link);
    const runtime = new FakeRuntime();
    runtime.picarLinkFactory = () => ctx.link;
    runtime.targets = opts.noCar ? [] : [new FakeSprite('Car')];
    runtime.redraws = 0;
    runtime.requestRedraw = () => runtime.redraws++;
    if (opts.pen) {
        // a fake stage renderer: the mirror draws on its own pen skin (no Pen extension needed)
        runtime.lines = [];
        runtime.clears = [];
        runtime.drawables = [];
        runtime.renderer = {
            createPenSkin: () => 7,
            createDrawable: layer => {
                runtime.drawables.push(layer);
                return 3;
            },
            updateDrawableSkinId: () => {},
            penLine: (...a) => runtime.lines.push(a),
            penClear: id => runtime.clears.push(id)
        };
    }
    ctx.runtime = runtime;
    ctx.ext = new PiCar(runtime);
    ctx.car = runtime.targets[0];
    ctx.ws = connect(ctx);
    ctx.tel = pose => ctx.ws.serverSend(tel({pose: Object.assign({x: 0, y: 0, dir: 90, src: 'dr'}, pose)}));
    return ctx;
};

test('setStageMirror persists, emits state, and loads back', t => {
    const ctx = makeLink(CarLink);
    t.equal(ctx.link.getState().stageMirror, false);
    const seen = [];
    ctx.link.subscribe(s => seen.push(s.stageMirror));
    ctx.link.setStageMirror(true);
    t.equal(ctx.link.getState().stageMirror, true);
    t.equal(ctx.local.getItem('picar.stageMirror'), '1');
    ctx.link.setStageMirror(true);
    ctx.link.setStageMirror(false);
    t.same(seen, [true, false], 'only changes are emitted');
    t.equal(ctx.local.getItem('picar.stageMirror'), '0');
    ctx.link.setStageMirror(true);
    const again = makeLink(CarLink, {localStore: new FakeStore({'picar.stageMirror': '1'})});
    t.equal(again.link.getState().stageMirror, true, 'restored');
    t.end();
});

test('setStageMirror survives unavailable storage', t => {
    const ctx = makeLink(CarLink, {localStore: new ThrowingStore()});
    t.equal(ctx.link.getState().stageMirror, false);
    t.doesNotThrow(() => ctx.link.setStageMirror(true));
    t.equal(ctx.link.getState().stageMirror, true);
    t.end();
});

test('mirror moves the Car sprite only while on', t => {
    const ctx = setup();
    ctx.tel({x: 10, y: 20, dir: 45});
    t.equal(ctx.car.x, 0, 'off: untouched');
    ctx.link.setStageMirror(true);
    ctx.tel({x: 10, y: 20, dir: 45});
    t.same([ctx.car.x, ctx.car.y, ctx.car.direction], [10, 20, 45]);
    ctx.link.setStageMirror(false);
    ctx.tel({x: 30, y: 40, dir: 10});
    t.same([ctx.car.x, ctx.car.y, ctx.car.direction], [10, 20, 45], 'off leaves the sprite where it is');
    t.end();
});

test('mirror ignores clones and other sprites, and a missing Car is not an error', t => {
    const ctx = setup({noCar: true});
    ctx.link.setStageMirror(true);
    t.doesNotThrow(() => ctx.tel({x: 5, y: 5}));
    const other = new FakeSprite('Cat');
    const clone = new FakeSprite('Car');
    clone.isOriginal = false;
    ctx.runtime.targets.push(other, clone);
    ctx.tel({x: 5, y: 5});
    t.same([other.x, clone.x], [0, 0]);
    t.end();
});

test('tiny changes are skipped', t => {
    const ctx = setup();
    ctx.link.setStageMirror(true);
    ctx.tel({x: 10, y: 10, dir: 90});
    const moves = ctx.car.moves;
    ctx.tel({x: 10.2, y: 10.2, dir: 90.3});
    t.equal(ctx.car.moves, moves);
    t.equal(ctx.car.turns, 0);
    ctx.tel({x: 10, y: 10, dir: 120});
    t.equal(ctx.car.turns, 1, 'a turn alone updates');
    t.equal(ctx.car.moves, moves, 'and does not move');
    t.end();
});

test('trail: drawn on the mirror own pen skin (no Pen extension), none across a jump or a reset', t => {
    const noPen = setup();
    noPen.link.setStageMirror(true);
    noPen.tel({x: 0, y: 0});
    t.doesNotThrow(() => noPen.tel({x: 10, y: 0}));

    const ctx = setup({pen: true});
    ctx.link.setStageMirror(true);
    ctx.tel({x: 1, y: 0});
    t.equal(ctx.runtime.lines.length, 0, 'first frame: nothing to draw from');
    ctx.tel({x: 11, y: 0});
    t.equal(ctx.runtime.lines.length, 1);
    const [skin, attrs, x0, y0, x1, y1] = ctx.runtime.lines[0];
    t.equal(skin, 7);
    t.same(ctx.runtime.drawables, ['pen'], 'one drawable on the pen layer, created on first use');
    t.equal(attrs.diameter, 2);
    t.same(attrs.color4f.map(v => Math.round(v * 255)), [198, 40, 40, 255]);
    t.same([x0, y0, x1, y1], [1, 0, 11, 0]);
    t.ok(ctx.runtime.redraws > 0);
    ctx.tel({x: 111, y: 0});
    t.equal(ctx.runtime.lines.length, 1, 'jump over 50 cm: moves, no line');
    t.equal(ctx.car.x, 111);
    ctx.tel({x: 121, y: 0});
    t.equal(ctx.runtime.lines.length, 2, 'trail resumes');
    ctx.ws.serverClose(1006);
    ctx.link.setStageMirror(false);
    ctx.link.setStageMirror(true);
    t.equal(ctx.ext._mirrorPrev, null, 'toggle / reconnect forget the previous position');
    t.end();
});

test('clearTrail (the GUI Clear map) and turning the mirror on erase the trail', t => {
    const ctx = setup({pen: true});
    ctx.link.setStageMirror(true);
    ctx.tel({x: 1, y: 0});
    ctx.tel({x: 11, y: 0});
    t.equal(ctx.runtime.lines.length, 1);
    ctx.link.clearTrail();
    t.same(ctx.runtime.clears, [7], 'the trail skin was cleared');
    t.equal(ctx.ext._mirrorPrev, null, 'and the next frame starts a new trail');
    ctx.link.setStageMirror(false);
    ctx.link.setStageMirror(true);
    t.same(ctx.runtime.clears, [7, 7], 'turning the mirror on starts fresh');
    t.end();
});

test('a mode switch forgets the previous position', t => {
    const ctx = setup({pen: true});
    ctx.link.setStageMirror(true);
    ctx.tel({x: 20, y: 0});
    ctx.tel({x: 25, y: 0});
    t.equal(ctx.runtime.lines.length, 1);
    ctx.link.setMode('sim');
    t.equal(ctx.ext._mirrorPrev, null);
    t.end();
});

test('blocks: show the car moves on the stage / stage mirror on?', t => {
    const ctx = setup();
    t.equal(ctx.ext.stageMirrorOn(), false);
    ctx.ext.setStageMirror({ONOFF: 'on'});
    t.equal(ctx.link.getState().stageMirror, true);
    t.equal(ctx.ext.stageMirrorOn(), true);
    ctx.ext.setStageMirror({ONOFF: 'off'});
    t.equal(ctx.ext.stageMirrorOn(), false);
    const info = ctx.ext.getInfo();
    t.ok(info.blocks.find(b => b && b.opcode === 'setStageMirror'));
    t.ok(info.blocks.find(b => b && b.opcode === 'stageMirrorOn'));
    t.end();
});
