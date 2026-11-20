const fs = require('fs');
const path = require('path');
const {test} = require('tap');
const VirtualMachine = require('../../src/virtual-machine');
const makeTestStorage = require('../fixtures/make-test-storage');
const readFileToBuffer = require('../fixtures/readProjectFile').readFileToBuffer;

const DIR = process.env.PICAR_EXAMPLES_DIR || '/src/out-examples';

// Shadow menu / literal blocks that are not runtime primitives.
const KNOWN_MENUS = new Set(['looks_costume', 'looks_backdrops', 'sound_sounds_menu', 'text', 'math_number',
    'math_integer', 'math_whole_number', 'math_positive_number', 'math_angle', 'colour_picker',
    'event_broadcast_menu']);

const listDir = () => {
    try {
        return fs.readdirSync(DIR);
    } catch (e) {
        return [];
    }
};

const files = listDir();
const sb3s = files.filter(f => f.endsWith('.sb3')).sort();

const isKnownOpcode = (runtime, block) => {
    const op = block.opcode;
    if (typeof runtime.getOpcodeFunction(op) === 'function') return true;
    if (runtime._primitives && runtime._primitives[op]) return true;
    if (runtime._hats && runtime._hats[op]) return true;
    if (block.shadow && (op.startsWith('picar_menu_') || KNOWN_MENUS.has(op))) return true;
    return false;
};

if (sb3s.length === 0) {
    test('picar example projects', t => {
        t.skip(`no example .sb3 files in ${DIR}`);
        t.end();
    });
} else {
    test('index.json lists every example file', t => {
        const index = JSON.parse(fs.readFileSync(path.join(DIR, 'index.json'), 'utf8'));
        t.ok(Array.isArray(index) && index.length > 0, 'index.json is a non-empty array');
        const ids = index.map(e => e.id);
        t.same(ids.slice().sort(), sb3s.map(f => f.replace(/\.sb3$/, '')).sort(),
            'index ids match the .sb3 files');
        for (const e of index) {
            t.ok(e.title && e.description, `${e.id}: has title and description`);
            t.ok(['beginner', 'intermediate', 'advanced'].includes(e.level), `${e.id}: level`);
            t.ok(Array.isArray(e.needs) && e.needs.every(n => ['ultrasonic', 'line', 'light'].includes(n)),
                `${e.id}: needs`);
            t.ok(e.world === null || ['arena', 'corridor', 'oval', 'lamp', 'maze'].includes(e.world),
                `${e.id}: world`);
        }
        t.end();
    });

    for (const file of sb3s) {
        test(`example ${file} loads and every opcode is known`, async t => {
            const vm = new VirtualMachine();
            vm.attachStorage(makeTestStorage());
            await vm.loadProject(readFileToBuffer(path.join(DIR, file)));

            t.ok(vm.extensionManager.isExtensionLoaded('picar'), 'picar extension loaded');
            t.ok(vm.runtime.targets.length >= 2, 'has a stage and a sprite');
            t.ok(vm.runtime.targets.some(tg => tg.sprite && tg.sprite.name === 'Car'), 'has a Car sprite');

            let count = 0;
            for (const target of vm.runtime.targets) {
                const blocks = target.blocks._blocks;
                for (const id of Object.keys(blocks)) {
                    const block = blocks[id];
                    count++;
                    if (!isKnownOpcode(vm.runtime, block)) {
                        t.fail(`${target.getName()}: unknown opcode ${block.opcode}`);
                    }
                }
            }
            t.ok(count > 0, `${count} blocks checked`);
            vm.quit();
            t.end();
        });
    }
}
