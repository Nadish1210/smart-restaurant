plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.nadish.smartrestaurant"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.nadish.smartrestaurant"
        minSdk = 24
        targetSdk = 34
        versionCode = 1
        versionName = "1.0.0"
    }

    // Public, NON-secret key used only so every build is signed the same way
    // (lets a new APK install over the old one). Not for Google Play.
    signingConfigs {
        create("sideload") {
            storeFile = file("sideload.jks")
            storePassword = "smartrestaurant"
            keyAlias = "sideload"
            keyPassword = "smartrestaurant"
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("sideload")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    implementation("androidx.core:core-splashscreen:1.0.1")
}
