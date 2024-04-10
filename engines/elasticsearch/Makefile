INDEX ?= gharchive
INDEX_CONFIG ?= $(PWD)/$(INDEX)/index-config.json
DATADIR ?= $(PWD)/data
ELASTIC_VERSION ?= 8.12.1

# We try to disable as many caches as possible (except for filesystem
# caches).
# :z for volumes is needed with SELinux.
start:
	mkdir -p ${DATADIR}
	@docker run -d \
	    --rm \
	    --name elasticsearch \
	    -p 9200:9200 \
	    -v ${DATADIR}:/usr/share/elasticsearch/data:z \
	    -e "discovery.type=single-node" \
	    -e ELASTIC_PASSWORD="elastic" \
	    -e cluster.name="basic-es" \
	    -e indices.queries.cache.size=0 \
            -e indices.fielddata.cache.size=0 \
	    -e bootstrap.memory_lock=false \
	    -e xpack.security.enabled=false \
	    -e transport.host="0.0.0.0" \
	    -e http.host="0.0.0.0" \
	    -e telemetry.optIn=false \
	    docker.elastic.co/elasticsearch/elasticsearch:$(ELASTIC_VERSION)
	@echo "Waiting 10 seconds for elasticsearch to start..."
	@sleep 10

stop:
	@echo "---- Stop Elasticsearch ----"
	@docker stop elasticsearch

clean:
	@docker rm -f elasticsearch
	rm -r ${DATADIR} && mkdir ${DATADIR}

create-index:
	@echo "---- Create index $(INDEX) with config $(INDEX_CONFIG) ----"
	@curl -XPUT http://localhost:9200/$(INDEX) -H "content-type: application/json" --data-binary @$(INDEX_CONFIG)

describe-index:
	@echo "---- Describe index $(INDEX) ----"
	@curl -XGET http://localhost:9200/$(INDEX)/_stats/docs,segments,store?pretty

delete-index:
	@echo "---- Delete index $(INDEX) ----"
	@curl -XDELETE http://localhost:9200/$(INDEX)

clean-index: delete-index create-index
