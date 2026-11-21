import {defineMessages} from 'react-intl';

export default defineMessages({
    aboutMenu: {
        id: 'picar.about.menuAbout',
        defaultMessage: 'About Car Scratch',
        description: 'About menu item'
    },
    sourceMenu: {
        id: 'picar.about.menuSource',
        defaultMessage: 'Source code',
        description: 'About menu item that opens the source code download'
    },
    safetyMenu: {
        id: 'picar.about.menuSafety',
        defaultMessage: 'Safety help',
        description: 'About menu item'
    },
    aboutTitle: {
        id: 'picar.about.title',
        defaultMessage: 'About Car Scratch',
        description: 'Title of the About dialog'
    },
    version: {
        id: 'picar.about.version',
        defaultMessage: 'Version: {version}',
        description: 'Car software version in the About dialog'
    },
    versionUnknown: {
        id: 'picar.about.versionUnknown',
        defaultMessage: 'not connected',
        description: 'Version shown when the car is not connected'
    },
    notAffiliated: {
        id: 'picar.about.notAffiliated',
        defaultMessage: 'Not affiliated with or endorsed by the Scratch Foundation.',
        description: 'Trademark disclaimer in the About dialog'
    },
    license: {
        id: 'picar.about.license',
        defaultMessage: 'Car Scratch is free software, licensed under the GNU Affero General Public License v3. ' +
            'The source code of this exact version is available from the car under "Source code".',
        description: 'License statement in the About dialog'
    },
    safetyTitle: {
        id: 'picar.safety.title',
        defaultMessage: 'Safety help',
        description: 'Title of the safety dialog'
    },
    safety1: {
        id: 'picar.safety.estop',
        defaultMessage: 'The red E-STOP button is always on screen. Press it to stop the car at once. ' +
            'Press Reset in the Pi Car tab to drive again.',
        description: 'Safety help paragraph'
    },
    safety2: {
        id: 'picar.safety.obstacle',
        defaultMessage: 'The car stops by itself when something is closer than the stop distance ' +
            '(20 cm by default). It can miss walls hit at an angle, soft things and thin things, ' +
            'and it cannot see behind or beside itself.',
        description: 'Safety help paragraph'
    },
    safety3: {
        id: 'picar.safety.pause',
        defaultMessage: 'If the page is closed, hidden, locked or loses Wi-Fi, the car stops and pauses. ' +
            'Press Resume or the green flag to continue.',
        description: 'Safety help paragraph'
    },
    safety4: {
        id: 'picar.safety.battery',
        defaultMessage: 'The battery switch is the last-resort stop. ' +
            'Keep the car on the floor, away from edges and stairs.',
        description: 'Safety help paragraph'
    },
    close: {
        id: 'picar.info.close',
        defaultMessage: 'Close',
        description: 'Close button of an information dialog'
    }
});
