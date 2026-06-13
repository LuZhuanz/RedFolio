const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("redfolio", {
  getConfig: () => ipcRenderer.invoke("redfolio:config")
});

