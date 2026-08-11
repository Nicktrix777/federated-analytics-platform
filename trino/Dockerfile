# Bakes our in-house Trino connector plugins (trino-plugins/*) into a Trino
# image built from the pinned upstream release. No local Maven/JDK required -
# this only ever runs via `docker compose build trino`, mirroring how
# core-api is built via compose rather than a local `go` CLI.
#
# trino-maven-plugin:24 (used by every plugin's pom.xml) itself requires
# Java 25 to run, independent of what bytecode version the plugin code
# targets - hence the 25 builder image, not just maven.compiler.release=25.
FROM maven:3.9-eclipse-temurin-25 AS plugin-builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends unzip \
    && rm -rf /var/lib/apt/lists/*

COPY trino-plugins/zoho-books/pom.xml zoho-books/pom.xml
COPY trino-plugins/zoho-books/src zoho-books/src
RUN --mount=type=cache,target=/root/.m2 mvn -f zoho-books/pom.xml -B package \
    && mkdir -p /build/plugins/zoho-books \
    && unzip -o zoho-books/target/*.zip -d /build/zoho-books-extracted \
    && find /build/zoho-books-extracted -name '*.jar' -exec cp {} /build/plugins/zoho-books/ \;

COPY trino-plugins/tally/pom.xml tally/pom.xml
COPY trino-plugins/tally/src tally/src
RUN --mount=type=cache,target=/root/.m2 mvn -f tally/pom.xml -B package \
    && mkdir -p /build/plugins/tally \
    && unzip -o tally/target/*.zip -d /build/tally-extracted \
    && find /build/tally-extracted -name '*.jar' -exec cp {} /build/plugins/tally/ \;

FROM trinodb/trino:483
COPY --from=plugin-builder /build/plugins/zoho-books /usr/lib/trino/plugin/zoho-books
COPY --from=plugin-builder /build/plugins/tally /usr/lib/trino/plugin/tally
