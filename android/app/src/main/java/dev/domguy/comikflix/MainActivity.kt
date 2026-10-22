package dev.domguy.comikflix

import android.annotation.SuppressLint
import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.view.WindowInsets
import android.view.WindowInsetsController
import android.view.WindowManager
import android.webkit.CookieManager
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Toast
import android.window.OnBackInvokedDispatcher
import org.json.JSONObject

/** The web app (frontend/, served by server.py) in a fullscreen WebView, plus what a browser tab
 *  can't do: Back walks the app's hash history, shared Asura/mgeko links start an import, links
 *  into the site open here, and the screen stays on in the reader. */
class MainActivity : Activity() {
    private lateinit var web: WebView

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        CookieManager.getInstance().setAcceptCookie(true)
        web = WebView(this).apply {
            setBackgroundColor(Color.parseColor("#141414"))
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            // Lets the site tell the app from a browser tab if it ever needs to.
            settings.userAgentString = "${settings.userAgentString} ComikFlixAndroid/${BuildConfig.VERSION_NAME}"
            webViewClient = Client()
            webChromeClient = WebChromeClient()
        }
        setContentView(web)
        hideSystemBars()
        if (Build.VERSION.SDK_INT >= 33) {
            onBackInvokedDispatcher.registerOnBackInvokedCallback(OnBackInvokedDispatcher.PRIORITY_DEFAULT) { back() }
        }
        if (savedInstanceState == null || web.restoreState(savedInstanceState) == null) {
            if (!handle(intent)) web.loadUrl(SITE)
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handle(intent) // a plain launcher tap keeps the current page
    }

    /** Opens a VIEW link or acts on shared text. False when the intent carries neither. */
    private fun handle(intent: Intent?): Boolean {
        when (intent?.action) {
            Intent.ACTION_VIEW -> {
                val url = intent.dataString ?: return false
                if (!inSite(url)) return false
                web.loadUrl(url)
                return true
            }
            Intent.ACTION_SEND -> {
                share(intent.getStringExtra(Intent.EXTRA_TEXT).orEmpty())
                return true
            }
        }
        return false
    }

    /** A shared Asura/mgeko link starts an import (the "Add a comic" defaults) and opens Downloads;
     *  other text is searched. */
    private fun share(text: String) {
        val link = Ingest.sourceLink(text)
        if (link == null) {
            val q = text.trim().take(100)
            navigate(if (q.isEmpty()) "#/" else "#/search?q=${Uri.encode(q)}")
            return
        }
        if (web.url == null) web.loadUrl(SITE)
        Thread {
            val result = Ingest.start(link)
            runOnUiThread {
                Toast.makeText(this, result.message, Toast.LENGTH_LONG).show()
                if (result.ok) navigate("#/downloads")
            }
        }.start()
    }

    /** Move the app to a hash route: in place when it's already loaded, else a fresh load. */
    private fun navigate(hash: String) {
        if (web.progress == 100 && web.url?.let(::inSite) == true) {
            web.evaluateJavascript("location.hash = ${JSONObject.quote(hash)}", null)
        } else {
            web.loadUrl(SITE + hash)
        }
    }

    private fun back() {
        if (web.canGoBack()) web.goBack() else finish()
    }

    @Deprecated("API < 33; newer versions use the OnBackInvokedCallback above")
    @Suppress("DEPRECATION")
    override fun onBackPressed() = back()

    // The PWA's manifest asks for fullscreen; match it. Swiping from an edge shows the bars briefly.
    private fun hideSystemBars() {
        window.insetsController?.apply {
            hide(WindowInsets.Type.systemBars())
            systemBarsBehavior = WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) hideSystemBars()
    }

    override fun onResume() {
        super.onResume()
        web.onResume()
    }

    override fun onPause() {
        web.onPause()
        CookieManager.getInstance().flush() // keep the PIN session if the process is killed
        super.onPause()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        web.saveState(outState)
    }

    override fun onDestroy() {
        web.destroy()
        super.onDestroy()
    }

    private inner class Client : WebViewClient() {
        // Pages outside the site (a series' source page, ...) open in the browser.
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            val url = request.url.toString()
            if (inSite(url) || !url.startsWith("http")) return false
            try {
                startActivity(Intent(Intent.ACTION_VIEW, request.url))
            } catch (_: ActivityNotFoundException) {
            }
            return true
        }

        // Also fires on hash changes, so this tracks moving in and out of the reader.
        override fun doUpdateVisitedHistory(view: WebView, url: String?, isReload: Boolean) {
            if (url?.contains("#/read/") == true) {
                window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
            } else {
                window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
            }
        }

        // The service worker serves the shell offline once it has been cached; this covers a first
        // launch (or a cleared cache) with no connection.
        override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
            if (!request.isForMainFrame) return
            view.loadDataWithBaseURL(null, offlinePage(error.description.toString()), "text/html", "utf-8", request.url.toString())
        }
    }

    companion object {
        val SITE: String = BuildConfig.SITE_URL

        fun inSite(url: String) = url.startsWith(SITE) || url == SITE.trimEnd('/')

        private fun offlinePage(reason: String) = """
            <!doctype html><meta name="viewport" content="width=device-width, initial-scale=1">
            <body style="margin:0;height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;
              gap:16px;background:#141414;color:#eee;font:16px system-ui;text-align:center;padding:24px;box-sizing:border-box">
            <div style="font-weight:800;letter-spacing:2px;color:#e50914">COMICFLIX</div>
            <div>Can't reach the server.</div>
            <div style="color:#888;font-size:13px">${android.text.Html.escapeHtml(reason)}</div>
            <a href="$SITE" style="color:#fff;background:#e50914;padding:10px 22px;border-radius:4px;text-decoration:none">Retry</a>
            </body>
        """.trimIndent()
    }
}
