#!/bin/sh

set -x

FEED_DIR=$PWD

for project in */
do
    cd $FEED_DIR/$project
    for job in */
    do
        cd $FEED_DIR/$project/$job
        kubectl apply -f cronjob.yaml
    done
done