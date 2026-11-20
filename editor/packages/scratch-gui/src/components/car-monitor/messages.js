import {defineMessages} from 'react-intl';

const messages = defineMessages({
    estop: {
        id: 'picar.estop.button',
        defaultMessage: 'E-STOP',
        description: 'Emergency stop button label'
    },
    estopAria: {
        id: 'picar.estop.aria',
        defaultMessage: 'Emergency stop the car',
        description: 'Accessibility label of the emergency stop button'
    },
    modeCar: {
        id: 'picar.monitor.modeCar',
        defaultMessage: 'Car',
        description: 'Mode switch: drive the real car'
    },
    modeSim: {
        id: 'picar.monitor.modeSim',
        defaultMessage: 'Simulator',
        description: 'Mode switch: drive the simulator'
    },
    modeAria: {
        id: 'picar.monitor.modeAria',
        defaultMessage: 'Drive the car or the simulator',
        description: 'Accessibility label of the Car / Simulator switch'
    },
    worldAria: {
        id: 'picar.monitor.worldAria',
        defaultMessage: 'Simulator world',
        description: 'Accessibility label of the simulator world picker'
    },
    monitorAria: {
        id: 'picar.monitor.aria',
        defaultMessage: 'Car monitor',
        description: 'Accessibility label of the car monitor panel'
    },
    canvasAria: {
        id: 'picar.monitor.canvasAria',
        defaultMessage: 'Top-down view of the car and its sensors',
        description: 'Accessibility label of the car monitor drawing'
    },
    statusConnecting: {
        id: 'picar.status.connecting',
        defaultMessage: 'Connecting',
        description: 'Connection chip: connecting'
    },
    statusOffline: {
        id: 'picar.status.offline',
        defaultMessage: 'Offline',
        description: 'Connection chip: not connected'
    },
    statusReload: {
        id: 'picar.status.reload',
        defaultMessage: 'Reload page',
        description: 'Connection chip: the page must be reloaded'
    },
    statusController: {
        id: 'picar.status.controller',
        defaultMessage: 'Driving',
        description: 'Connection chip: this tab controls the car'
    },
    statusViewer: {
        id: 'picar.status.viewer',
        defaultMessage: 'View only',
        description: 'Connection chip: this tab can only watch the car'
    },
    statusSim: {
        id: 'picar.status.sim',
        defaultMessage: 'Simulator',
        description: 'Connection chip: this tab drives its own simulator'
    },
    bannerObstacle: {
        id: 'picar.banner.obstacle',
        defaultMessage: 'OBSTACLE STOP',
        description: 'Banner: the car stopped because of an obstacle'
    },
    bannerEstop: {
        id: 'picar.banner.estop',
        defaultMessage: 'E-STOP',
        description: 'Banner: the car is emergency stopped'
    },
    bannerHwFault: {
        id: 'picar.banner.hwFault',
        defaultMessage: 'Hardware fault — lift the car or switch it off. Reset when safe.',
        description: 'Banner: the motor hardware reported a fault'
    },
    bannerEstopReset: {
        id: 'picar.banner.estopReset',
        defaultMessage: 'Reset',
        description: 'Button that clears the emergency stop'
    },
    bannerPaused: {
        id: 'picar.banner.paused',
        defaultMessage: 'PAUSED',
        description: 'Banner: the car is on hold'
    },
    bannerResume: {
        id: 'picar.banner.resume',
        defaultMessage: 'Resume',
        description: 'Button that lets the car drive again after a pause'
    },
    bannerViewOnly: {
        id: 'picar.banner.viewOnly',
        defaultMessage: 'VIEW ONLY',
        description: 'Banner: another tab is driving the car'
    },
    bannerTakeControl: {
        id: 'picar.banner.takeControl',
        defaultMessage: 'Take control',
        description: 'Button that takes control of the car from another tab'
    },
    takeControlConfirm: {
        id: 'picar.banner.takeControlConfirm',
        defaultMessage: 'Someone else may be driving the car. Take control? The car will stop first.',
        description: 'Confirmation before taking control of the car'
    },
    bannerNotCalibrated: {
        id: 'picar.banner.notCalibrated',
        defaultMessage: 'Not calibrated: stop distances are estimates',
        description: 'Banner: the car has not been calibrated'
    },
    bannerObstacleStopOff: {
        id: 'picar.banner.obstacleStopOff',
        defaultMessage: 'OBSTACLE STOP OFF: the car will not stop for obstacles',
        description: 'Banner: the distance stop is switched off'
    },
    bannerLineOffline: {
        id: 'picar.banner.lineOffline',
        defaultMessage: 'Line sensor offline',
        description: 'Banner: the line follower module does not answer'
    },
    bannerNoCar: {
        id: 'picar.banner.noCar',
        defaultMessage: 'The car hardware is not available',
        description: 'Banner: the server cannot reach the car hardware'
    },
    bannerReconnecting: {
        id: 'picar.banner.reconnecting',
        defaultMessage: 'Reconnecting…',
        description: 'Banner: the connection to the car was lost'
    },
    bannerReload: {
        id: 'picar.banner.reload',
        defaultMessage: 'The car software changed. Reload this page.',
        description: 'Banner: the page is out of date'
    },
    bannerReloadButton: {
        id: 'picar.banner.reloadButton',
        defaultMessage: 'Reload page',
        description: 'Button that reloads the page'
    },
    tabSprites: {
        id: 'picar.tabs.sprites',
        defaultMessage: 'Sprites',
        description: 'Tab showing the sprite list'
    },
    tabMonitor: {
        id: 'picar.tabs.monitor',
        defaultMessage: 'Pi Car',
        description: 'Tab showing the car monitor'
    },
    tabsAria: {
        id: 'picar.tabs.aria',
        defaultMessage: 'Sprites or car monitor',
        description: 'Accessibility label of the tab list under the stage'
    },
    rotateHint: {
        id: 'picar.rotate.hint',
        defaultMessage: 'Rotate your iPad to landscape',
        description: 'Overlay shown when the screen is in portrait orientation'
    },
    rotateDismiss: {
        id: 'picar.rotate.dismiss',
        defaultMessage: 'Dismiss',
        description: 'Button that hides the rotate hint'
    }
});

export default messages;
