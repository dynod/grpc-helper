# Makefile for GRPC helper project

# Setup roots
WORKSPACE_ROOT := $(CURDIR)/../..
PROJECT_ROOT := $(CURDIR)

# Python package name
PYTHON_PACKAGE := grpc-helper

# Package for generated code
PROTO_PACKAGE := grpc_helper/api

# Main makefile suite - defs
include $(WORKSPACE_ROOT)/.workspace/main.mk

# Default target is to build Python artifact
default: build

# Main makefile suite - rules
include $(WORKSPACE_ROOT)/.workspace/rules.mk

# Build shall trigger test codegen as well
build: test-codegen

# Test code generation
.PHONY: test-codegen
test-codegen: codegen
	make -C $(PROJECT_ROOT)/src/tests codegen
