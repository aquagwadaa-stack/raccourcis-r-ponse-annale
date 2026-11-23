pluginManagement {
    repositories {
        google()
        // Miroir officiel de Maven Central (Google) : plus fiable que repo1 depuis certains réseaux.
        maven("https://maven-central.storage-download.googleapis.com/maven2/")
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        maven("https://maven-central.storage-download.googleapis.com/maven2/")
        mavenCentral()
    }
}

rootProject.name = "CorrecteurTablette"
include(":app")
