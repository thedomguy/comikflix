package dev.domguy.comikflix

import android.webkit.CookieManager
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/** Starts an import from a shared link: POST /api/ingest, as the web app's "Add a comic" does,
 *  signed in with the WebView's session cookie. */
object Ingest {
    class Result(val ok: Boolean, val message: String)

    private val LINK = Regex("""https?://[^\s"'<>]+""")

    /** The first Asura Scans or mgeko link in shared text (apps often send "Title https://..."). */
    fun sourceLink(text: String): String? =
        LINK.findAll(text).map { it.value }.firstOrNull { link ->
            val host = runCatching { URL(link).host.lowercase() }.getOrNull().orEmpty()
            "asura" in host || "mgeko" in host
        }

    /** Blocking; call off the main thread. */
    fun start(link: String): Result {
        val conn = URL(MainActivity.SITE + "api/ingest").openConnection() as HttpURLConnection
        return try {
            conn.requestMethod = "POST"
            conn.connectTimeout = 15_000
            conn.readTimeout = 30_000
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json")
            // Shows in the Downloads history as via:android (server.ingest_source).
            conn.setRequestProperty("X-ComikFlix-Source", "android:share")
            CookieManager.getInstance().getCookie(MainActivity.SITE)?.let { conn.setRequestProperty("Cookie", it) }
            conn.outputStream.use { it.write(JSONObject().put("series", link).put("start_chapter", 1).toString().toByteArray()) }
            val code = conn.responseCode
            val body = (if (code < 400) conn.inputStream else conn.errorStream)?.bufferedReader()?.use { it.readText() }.orEmpty()
            when {
                code == 401 -> Result(false, "Sign in to Comicflix, then share the link again.")
                code in 200..299 -> Result(true, "Adding comic…")
                else -> Result(false, runCatching { JSONObject(body).optString("error") }.getOrNull()?.ifBlank { null } ?: "Couldn't add it (HTTP $code)")
            }
        } catch (e: Exception) {
            Result(false, "Couldn't reach the server: ${e.message}")
        } finally {
            conn.disconnect()
        }
    }
}
