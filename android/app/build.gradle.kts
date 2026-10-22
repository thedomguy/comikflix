import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// The app is a WebView on the live site (same origin as the API, so the PIN session cookie and
// the server's same-origin checks work unchanged). -PcomikflixUrl=... points a build elsewhere.
val siteUrl = providers.gradleProperty("comikflixUrl").orNull ?: "https://watch.domguy.dev/readers/"

android {
    namespace = "dev.domguy.comikflix"
    compileSdk = 36

    defaultConfig {
        applicationId = "dev.domguy.comikflix"
        minSdk = 30
        targetSdk = 36
        // KT Remote stamps these per release (scripts/ktr-version.init.gradle).
        versionCode = providers.gradleProperty("ktrVersionCode").orNull?.toInt() ?: 1
        versionName = providers.gradleProperty("ktrVersionName").orNull ?: "1.0.0"
        buildConfigField("String", "SITE_URL", "\"${siteUrl.trimEnd('/')}/\"")
    }

    // Debug builds sign with ~/.android/debug.keystore: the shared key every KT Remote app uses.
    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        buildConfig = true
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}
