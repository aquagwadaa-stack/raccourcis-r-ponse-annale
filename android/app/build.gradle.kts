plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "fr.correcteur.annales"
    compileSdk = 36

    defaultConfig {
        applicationId = "fr.correcteur.annales"
        minSdk = 30 // capture d'écran par le service d'accessibilité : Android 11 et plus
        targetSdk = 36
        versionCode = 1
        versionName = "1.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    signingConfigs {
        // Clé de signature fixe, versionnée : chaque nouvelle version s'installe par-dessus
        // l'ancienne. L'appli n'est distribuée que par ce dépôt.
        create("partagee") {
            storeFile = file("../correcteur.keystore")
            storePassword = "correcteur"
            keyAlias = "correcteur"
            keyPassword = "correcteur"
        }
    }

    buildTypes {
        release {
            // Pas de réduction de code : la bibliothèque Claude s'appuie sur la réflexion (Jackson).
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("partagee")
        }
        debug {
            signingConfig = signingConfigs.getByName("partagee")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    packaging {
        resources {
            excludes += setOf(
                "META-INF/DEPENDENCIES",
                "META-INF/LICENSE*",
                "META-INF/NOTICE*",
                "META-INF/versions/**",
                "META-INF/INDEX.LIST",
            )
        }
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
    }
}

dependencies {
    implementation("com.anthropic:anthropic-java:3.12.0")

    testImplementation("junit:junit:4.13.2")
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")

    androidTestImplementation("androidx.test:runner:1.6.2")
    androidTestImplementation("androidx.test:core:1.6.1")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
}
