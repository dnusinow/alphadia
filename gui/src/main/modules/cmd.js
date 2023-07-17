const { exec, spawn } = require('child_process');
var path = require('path');
const Transform = require('stream').Transform;
const StringDecoder = require('string_decoder').StringDecoder;
const { dialog } = require('electron')
const os = require('os');
const { proc } = require('node-os-utils');
var kill = require('tree-kill');

function condaPATH(username, platform){
    if (platform == "darwin"){
        return [
            "/Users/" + username + "/miniconda3/bin/",
            "/Users/" + username + "/anaconda3/bin/",
            "/Users/" + username + "/miniconda/bin/",
            "/Users/" + username + "/anaconda/bin/",
            "/anaconda/bin/", 
        ]
    } else if (platform == "win32"){
        return [
            "C:\\Users\\" + username + "\\miniconda3\\Scripts\\",
            "C:\\Users\\" + username + "\\anaconda3\\Scripts\\",
            "C:\\Users\\" + username + "\\miniconda\\Scripts\\",
            "C:\\Users\\" + username + "\\anaconda\\Scripts\\",
        ]
    } else {
        return [
            "/opt/conda/bin/",
            "/usr/local/bin/",
            "/usr/local/anaconda/bin/",
        ]
    }
}

function testCommand(command, pathUpdate){
    const PATH = process.env.PATH + ":" + pathUpdate
    return new Promise((resolve, reject) => {
        exec(command, {env: {...process.env, PATH}}, () => {}).on('exit', (code) => {resolve(code)});
    });
}

const CondaEnvironment = class {

    pathUpdate = ""
    envName = "";
    exists = {
        conda: false,
        python: false,
        alphadia: false
    }
    versions = {
        conda: "",
        python: "",
        alphadia: ""
    }
    ready = false;

    initPending = true;
    initPromise = null;

    std = [];
    pid = null;
    
    constructor(envName){
        this.envName = envName;

        this.initPromise = this.discoverCondaPATH().then((pathUpdate) => {
            this.pathUpdate = pathUpdate;
            this.exists.conda = true;
            
        }).then(() => {
            return Promise.all([
                this.checkCondaVersion(),
                this.checkPythonVersion(),
                this.checkAlphadiaVersion(),
            ])
        }).then(() => {
            this.ready = [this.exists.conda, this.exists.python, this.exists.alphadia].every(Boolean);
            this.initPending = false;
        }).catch((error) => {
            dialog.showErrorBox("Conda not found", "Conda could not be found on your system. Please make sure conda is installed and added to your PATH.")
        })
    }
    
    discoverCondaPATH(){
        return new Promise((resolve, reject) => {

            const paths = ["", ...condaPATH(os.userInfo().username, os.platform())]
            Promise.all(paths.map((path) => {
                return testCommand("conda", path)
                })).then((codes) => {
                    const index = codes.findIndex((code) => code == 0)

                    if (index == -1){
                        reject("conda not found")
                    } else {
                        resolve(paths[index])
                    }
            })
        })
    }

    checkCondaVersion(){
        return new Promise((resolve, reject) => {
            this.exec('conda info --json', (err, stdout, stderr) => {
                if (err) {return;}
                const info = JSON.parse(stdout);
                this.versions.conda = info["conda_version"];
                this.exists.conda = true;
                resolve();
            });
        })
    }

    checkPythonVersion(){
        return new Promise((resolve, reject) => {
            this.exec(`conda run -n ${this.envName} python --version`, (err, stdout, stderr) => {
                if (err) {return;}
                const versionPattern = /\d+\.\d+\.\d+/;
                const versionList = stdout.match(versionPattern);

                if (versionList == null){return;}
                if (versionList.length == 0){return;}

                this.versions.python = versionList[0];
                this.exists.python = true;
                resolve();
            });
        })
    }
    checkAlphadiaVersion(){
        return new Promise((resolve, reject) => {
            this.exec(`conda list -n ${this.envName} --json`, (err, stdout, stderr) => {
                if (err) {return;}
                const info = JSON.parse(stdout);
                const packageInfo = info.filter((p) => p.name == "alphadia");
                if (packageInfo.length == 0){return;}

                this.versions.alphadia = packageInfo[0].version;
                this.exists.alphadia = true;
                resolve();
            });
        })
    }

    exec(command, callback){
        const PATH = process.env.PATH + ":" + this.pathUpdate
        exec(command, {env: {...process.env, PATH}}, callback);
    }

    buildEnvironmentStatus(){
        return {
            envName: this.envName,
            versions: this.versions,
            exists: this.exists,
            ready: this.ready
        }
    }

    getEnvironmentStatus(){
        if (this.initPending){
            return this.initPromise.then(() => {
                return this.buildEnvironmentStatus();
            })
        } else {
            return this.buildEnvironmentStatus();
        }
    }

    spawn(cmd){
        console.log(cmd)
        return new Promise((resolve, reject) => {
            if (!this.ready){
                reject("Environment not ready");
                return;
            }
            
            
            const PATH = process.env.PATH + ":" + this.pathUpdate
            const tokens = cmd.split(" ")
            const cmdp = spawn(tokens[0], tokens.slice(1), { env:{...process.env, PATH}});
            
            const stdoutTransform = lineBreakTransform();
            cmdp.stdout.pipe(stdoutTransform).on('data', (data) => {
                this.std.push(data.toString())
            });
            
            const stderrTransform = lineBreakTransform();
            cmdp.stderr.pipe(stderrTransform).on('data', (data) => {
                this.std.push(data.toString())
            });

            cmdp.on('close', (code) => {
                resolve(code);
                return;
            });

            this.pid = cmdp.pid;

        })
    }

    kill(){
        if (this.pid != null){
            console.log(`Killing process ${this.pid}`)
            kill(this.pid);
        }

    }

    getOutputLength(){
        return this.std.length
    }

    getOutputRows(limit, offset){
        const startIndex = offset
        const stopIndex = Math.min(offset + limit, this.std.length)
        return this.std.slice(startIndex, stopIndex)
    }

}


function getCondaInfo() {
    return new Promise((resolve, reject) => {
        exec('conda info --json', (err, stdout, stderr) => {
            if (err) {
                reject(err);
            }
            const info = JSON.parse(stdout);
            resolve(info);
        });
    });
}

function getPythonVersion(envName){
    return new Promise((resolve, reject) => {
        exec(`conda run -n ${envName} python --version`, (err, stdout, stderr) => {
            if (err) {
                reject(err);
                return;
            }
            const versionPattern = /\d+\.\d+\.\d+/;
            const versionList = stdout.match(versionPattern);
            // check if versionList is null
            if (versionList == null){
                reject("Python version not found");
                return;
            }
            if (versionList.length == 0){
                reject("Python version not found");
                return;
            }
            resolve(versionList[0]);
        });
    });
}

function getPackageVersion(envName, packageName){
    return new Promise((resolve, reject) => {
        exec(`conda list -n ${envName} --json`, (err, stdout, stderr) => {
            if (err) {
                reject(err);
            }
            const info = JSON.parse(stdout);
            const packageInfo = info.filter((package) => package.name == packageName);
            if (packageInfo.length == 0){
                reject(`Package ${packageName} not found in environment ${envName}`);
            }
            resolve(packageInfo[0].version);
        });
    });
}

function getEnvironmentStatus(envName){
    environment = {
        envName: envName,
        hasConda: false,
        condaVersion: "",
        hasEnv: false,
        hasPython: false,
        pythonVersion: "",
        hasAlphadia: false,
        alphadiaVersion: "",
        ready: false
    }

    return getCondaInfo().then((info) => {
        environment.hasConda = true;
        environment.condaVersion = info["conda_version"];
        environment.hasEnv = info["envs"].map((env) => path.basename(env)).includes(envName);
        return getPythonVersion(envName).then((version) => {
            environment.hasPython = true;
            environment.pythonVersion = version;
            return getPackageVersion(envName, "alphadia").then((version) => {
                environment.hasAlphadia = true;
                environment.alphadiaVersion = version;
                environment.ready = [environment.hasConda, environment.hasEnv, environment.hasPython, environment.hasAlphadia].every(Boolean);
                return environment
            }).catch((error) => {
                return environment
            })
        }).catch((error) => {
            return environment
        })
    }).catch((error) => {
        return environment
    })
}

function lineBreakTransform () {

    // https://stackoverflow.com/questions/40781713/getting-chunks-by-newline-in-node-js-data-stream
    const decoder = new StringDecoder('utf8');

    return new Transform({
        transform(chunk, encoding, cb) {
        if ( this._last === undefined ) { this._last = "" }
        this._last += decoder.write(chunk);
        var list = this._last.split(/\n/);          
        this._last = list.pop();
        for (var i = 0; i < list.length; i++) {
            this.push( list[i] );
        }
        cb();
    },
    
    flush(cb) {
        this._last += decoder.end()
        if (this._last) { this.push(this._last) }
        cb()
    }
    });
}










module.exports = {
    getCondaInfo,
    getPythonVersion,
    getPackageVersion,
    getEnvironmentStatus,
    lineBreakTransform,
    testCommand,
    CondaEnvironment
}

