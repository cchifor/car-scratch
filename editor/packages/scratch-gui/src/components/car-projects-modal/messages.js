import {defineMessages} from 'react-intl';

export default defineMessages({
    openTitle: {
        id: 'picar.projects.openTitle',
        defaultMessage: 'Open from car',
        description: 'Title of the dialog listing projects stored on the car'
    },
    saveTitle: {
        id: 'picar.projects.saveTitle',
        defaultMessage: 'Save to car',
        description: 'Title of the dialog that stores the project on the car'
    },
    loading: {
        id: 'picar.projects.loading',
        defaultMessage: 'Loading…',
        description: 'Shown while the project list is loading'
    },
    empty: {
        id: 'picar.projects.empty',
        defaultMessage: 'No projects saved on the car yet.',
        description: 'Shown when the car has no saved projects'
    },
    nameLabel: {
        id: 'picar.projects.nameLabel',
        defaultMessage: 'Project name',
        description: 'Label of the project name field'
    },
    nameInvalid: {
        id: 'picar.projects.nameInvalid',
        defaultMessage: 'Use 1 to 64 letters, numbers, spaces, dashes or underscores.',
        description: 'Shown when the project name is not allowed'
    },
    save: {
        id: 'picar.projects.save',
        defaultMessage: 'Save',
        description: 'Save button'
    },
    saving: {
        id: 'picar.projects.saving',
        defaultMessage: 'Saving…',
        description: 'Shown while the project is being stored'
    },
    cancel: {
        id: 'picar.projects.cancel',
        defaultMessage: 'Cancel',
        description: 'Cancel button'
    },
    error: {
        id: 'picar.projects.error',
        defaultMessage: 'Something went wrong: {detail}',
        description: 'Inline error in the car projects dialog'
    },
    openMenuItem: {
        id: 'picar.menu.openFromCar',
        defaultMessage: 'Open from car…',
        description: 'File menu item'
    },
    saveMenuItem: {
        id: 'picar.menu.saveToCar',
        defaultMessage: 'Save to car…',
        description: 'File menu item'
    }
});
