# First use eng/provenance.py fetch-source, then copy icu.zip into this context.

FROM docker.io/ubuntu:24.04 AS build

RUN apt-get update && apt-get install -y make git python3 doxygen zip wget g++

WORKDIR /icu

ENV ICU_DATA_FILTER_FILE=/icu/filters.json
COPY filters.json .

COPY icu.zip .
RUN echo "4d776b33e5016b259b96f86135264f425e5703543d9cfb0cf23f0d923665a742  icu.zip" | sha256sum --check --strict
RUN unzip icu.zip
WORKDIR icu-457157a92aa053e632cc7fcfd0e12f8a943b2d11/icu4c/source

RUN ./runConfigureICU Linux
RUN make -j $(nproc)


FROM scratch
COPY --from=build /icu/icu-457157a92aa053e632cc7fcfd0e12f8a943b2d11/icu4c/source/data/out/tmp/icudt77l.dat /icudt.dat
