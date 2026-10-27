---
name: Fresh install report
about: You followed the README on a clean machine using only the docs. Tell us where it worked and where it did not.
title: "Fresh install: <what you did>"
labels: ["docs", "release"]
---

<!--
The goal is to find what the documentation does not tell you. Please do not fix a problem with knowledge from outside
the docs without writing it down below: that is exactly what we want to learn. One issue per stumble is even better;
this form is for the whole run.
-->

## Your setup

- Machine(s): <!-- VM or bare metal, vCPU, RAM, disks, number of network interfaces -->
- Ubuntu version (`lsb_release -ds`):
- Release or commit you installed: <!-- for example v1.0.0 -->
- Which guide you followed: <!-- README > quickstart.md, or SETUP.md / deb-install.md for several nodes -->
- Experience with OpenStack or other clouds: <!-- none / some / a lot; it helps us read your report -->

## How far did you get?

- [ ] Packages downloaded and checksums verified
- [ ] Control plane up (`curl http://127.0.0.1:8005/health`)
- [ ] Logged in as admin
- [ ] Compute node registered (`cloudx hypervisor list`)
- [ ] Block storage configured, volume type created
- [ ] Public network created
- [ ] First VM `ACTIVE`
- [ ] Floating IP reachable (ping and SSH)
- [ ] Dashboard opened

Total time, from the first command to the last step you reached:

## Where did you stumble?

<!-- One entry per stumble: the step (file and heading), what you ran, what you expected, what happened (paste the output),
and how you got past it, or that you did not. -->

1. 

## Things that were unclear or wrong in the docs

<!-- Missing prerequisites, commands that do not exist, names that differ between pages, steps in the wrong order. -->

## Anything else
