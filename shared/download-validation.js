import { STATUS } from "./constants.js";

export const SMALL_FILE_WARNING = "SMALL_FILE_REVIEW_REQUIRED";

export function classifyCompletedDownload(item) {
  const warnings = [];
  if (!item?.exists) warnings.push("FILE_NOT_CONFIRMED");
  if (item?.fileSize === 0) warnings.push("EMPTY_FILE");

  const htmlResponse =
    String(item?.mime || "").includes("text/html") ||
    /\.(?:html?|jspx?|aspx?|php)$/i.test(item?.filename || "");
  const unusableFile = warnings.length > 0;

  return {
    status: htmlResponse
      ? STATUS.FAILED
      : unusableFile
        ? STATUS.FAILED
        : STATUS.VERIFIED,
    warnings,
    errorCode: htmlResponse
      ? "HTML_RESPONSE_RECEIVED"
      : !item?.exists
        ? "FILE_NOT_CONFIRMED"
        : item?.fileSize === 0
          ? "EMPTY_FILE"
          : null,
    // Bir web yanıtı gerçek evrak değildir; ancak tek bir hatalı yanıt yüzünden
    // bütün arşiv kuyruğu durdurulmaz. Kayıt sorunluya alınır ve sıra ilerler.
    pauseQueue: false
  };
}


export function browserCompletionPreflight(item) {
  const state = String(item?.state || "");
  const exists = item?.exists !== false;
  const fileSize = Number.isFinite(Number(item?.fileSize)) ? Number(item.fileSize) : null;
  const totalBytes = Number.isFinite(Number(item?.totalBytes)) ? Number(item.totalBytes) : null;
  if (state && state !== "complete") {
    return { ok:false, errorCode:"DOWNLOAD_NOT_COMPLETE", detail:"Tarayıcı indirmeyi tamamlanmış olarak işaretlemedi.", fileSize, totalBytes };
  }
  if (!exists) {
    return { ok:false, errorCode:"FILE_NOT_CONFIRMED", detail:"Tamamlanan indirme dosyası diskte görünmüyor.", fileSize, totalBytes };
  }
  if (fileSize === 0) {
    return { ok:false, errorCode:"EMPTY_FILE", detail:"İndirilen dosya 0 bayt.", fileSize, totalBytes };
  }
  if (totalBytes !== null && totalBytes > 0 && fileSize !== null && fileSize < totalBytes) {
    return { ok:false, errorCode:"DOWNLOAD_SIZE_MISMATCH", detail:`İndirme beklenen boyuttan kısa: ${fileSize}/${totalBytes} bayt.`, fileSize, totalBytes };
  }
  return { ok:true, fileSize, totalBytes };
}
