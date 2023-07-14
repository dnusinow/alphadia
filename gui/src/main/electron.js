const { app, BrowserWindow, ipcMain, dialog, shell, nativeTheme } = require('electron')
const osu = require('node-os-utils')
const path = require("path");
const writeYamlFile = require('write-yaml-file')
const fs = require('fs')
const os = require('os')

const { handleGetSingleFolder,handleGetMultipleFolders, handleGetSingleFile, handleGetMultipleFiles} = require('./modules/dialogHandler')
const { discoverWorkflows, workflowToConfig } = require('./modules/workflows')
const { getEnvironmentStatus, lineBreakTransform, CondaEnvironment} = require('./modules/cmd');

let mainWindow;
let workflows;
let environment;

function createWindow() {
  // Create the browser window.
  mainWindow = new BrowserWindow({
    width: 900,
    height: 600,
    minWidth: 900,
    minHeight: 600,
    title: "alphaDIA",
    webPreferences: {
        nodeIntegration: false, // is default value after Electron v5
        contextIsolation: true, // protect against prototype pollution
        enableRemoteModule: false, // turn off remote
        preload: path.join(__dirname, 'preload.js')
    },
  });
  
    mainWindow.loadFile(path.join(__dirname, "../dist/index.html"));
    // Open the DevTools.
    mainWindow.webContents.openDevTools({ mode: "detach" });

    // set title
    mainWindow.setTitle("alphaDIA");

    environment = new CondaEnvironment("alpha")
    workflows = discoverWorkflows(mainWindow)
}

handleOpenLink = (event, url) => {
    event.preventDefault()
    shell.openExternal(url)
}

async function handleGetUtilisation (event) {

    var mem = osu.mem

    const values = await Promise.all([osu.cpu.usage(), mem.info()])
    const cpu = values[0]
    const memory = values[1]
    return {
        ...memory,
        cpu
    }
}

function handleStartWorkflow(workflow) {

    const workflowFolder = workflow.output.path
    const config = workflowToConfig(workflow)

    // check if workflow folder exists
    if (!fs.existsSync(workflowFolder)) {
        dialog.showMessageBox(mainWindow, {
            type: 'error',
            title: 'Workflow Failed to Start',
            message: `Could not start workflow. Output folder ${workflowFolder} does not exist.`,
        }).catch((err) => {
            console.log(err)
        })
        return Promise.resolve("Workflow failed to start.")
    }

    // save config.yaml in workflow folder
    writeYamlFile.sync(path.join(workflowFolder, "config.yaml"), config)
    
    return environment.spawn("conda run --no-capture-output python /Users/georgwallmann/Documents/git/notebooks/python_stuff/benchmark_timer.py")
}

// This method will be called when Electron has finished
// initialization and is ready to create browser windows.
// Some APIs can only be used after this event occurs.
app.whenReady().then(() => {

    console.log(app.getLocale())
    console.log(app.getSystemLocale())
    createWindow(); 

    ipcMain.handle('get-single-folder', handleGetSingleFolder(mainWindow))
    ipcMain.handle('get-multiple-folders', handleGetMultipleFolders(mainWindow))
    ipcMain.handle('get-single-file', handleGetSingleFile(mainWindow))
    ipcMain.handle('get-multiple-files', handleGetMultipleFiles(mainWindow))
    ipcMain.handle('get-utilisation', handleGetUtilisation)
    ipcMain.handle('get-workflows', () => workflows)

    ipcMain.handle('get-environment', () => environment.getEnvironmentStatus())

    ipcMain.handle('run-command', (event, cmd) => environment.spawn(cmd))
    ipcMain.handle('get-output-rows', (event, {limit, offset}) => environment.getOutputRows(limit, offset))
    ipcMain.handle('get-output-length', () => environment.getOutputLength())
    
    ipcMain.handle('start-workflow', (event, workflow) => handleStartWorkflow(workflow))
    ipcMain.handle('abort-workflow', () => environment.kill())

    ipcMain.on('open-link', handleOpenLink)
    nativeTheme.on('updated', () => {
        console.log("Theme changed to: " + nativeTheme)
        mainWindow.webContents.send('theme-change', nativeTheme.shouldUseDarkColors)
    })
});

app.on('window-all-closed', () => {
    app.quit();
});