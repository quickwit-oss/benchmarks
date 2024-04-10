#!/bin/bash

# Full path to elastic-integration-corpus-generator-tool binary tool
# export GENERATOR=$GENERATOR
# Where the dataset should be written
export CORPORA_ROOT=tmp
export CONFIG=config.tmp.yml
export BUCKET=s3://quickwit-datasets-public/benchmarks/generated-logs/
export FILE_PREFIX=generated-logs-v1
mkdir -p $CORPORA_ROOT

# 1GB = 1070741824 bytes
START_DATE=1672531200 #Sunday, January 1, 2023 12:00:00 AM

for i in {0021..0030}
do
    echo "Generating file #$i"
    start_date_i=$((START_DATE + (10#$i-1)*3600))
    end_date_i=$((START_DATE + 10#$i*3600))
    sed "s/START_DATE/${start_date_i}/; s/END_DATE/${end_date_i}/" config-1.yml > config.tmp.yml
    $GENERATOR generate-with-template template.tpl fields.yml -t 1070741824 -c "${CONFIG}" -y gotext
    CORPORA_PATH=$CORPORA_ROOT/corpora

    for file in ${CORPORA_PATH}/*.tpl
    do
      echo "Size of file ${file}"
      stat “%s” "${file}"
      echo "Gzipping ${file}"
      FILENAME="${FILE_PREFIX}-${i}"
      mv "${file}" "${FILENAME}.ndjson"
      gzip "${FILENAME}.ndjson"
      mv "${FILENAME}.ndjson.gz" ../../datasets/
      #echo "Copying to ${BUCKET}"
      #aws s3 cp "${FILENAME}.ndjson.gz" "${BUCKET}"

      #echo "Removing ${FILENAME}.ndjson.gz"
      #rm "${FILENAME}.ndjson.gz"
    done
done
rm "${CONFIG}"
