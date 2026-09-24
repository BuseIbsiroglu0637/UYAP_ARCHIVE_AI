// Download identity is independent of the server's eventual filename/format.
export function directDownloadEvidence(document, item) {
  return document?.bindingMethod === "extension-download-id" &&
    Number.isInteger(document.downloadId) && document.downloadId === item?.id &&
    item.state === "complete" && Boolean(document.attemptId) && Boolean(document.stagingPath);
}

export function retryableNativeOperation(agent, retryCount, maximum = 3) {
  return ["NATIVE_DB_BUSY", "NATIVE_FILE_BUSY"].includes(agent?.errorCode) && retryCount <= maximum;
}
