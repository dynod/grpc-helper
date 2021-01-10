# Makefile for test API

# Setup roots
WORKSPACE_ROOT := $(CURDIR)/../../../..
PROJECT_ROOT := $(CURDIR)

# Package for generated code
PROTO_PACKAGE := tests/api

# Pre-define source folder to force code generation in the right place
SRC_FOLDER := $(PROJECT_ROOT)/..

# Pre-define venv to use parent project one
PYTHON_VENV := $(PROJECT_ROOT)/../../venv

# Point to generic folder for proto deps
PROTO_DEPS := $(PROJECT_ROOT)/../../protos

# Main makefile suite - defs
include $(WORKSPACE_ROOT)/.workspace/main.mk

# Default target is to generate code
default: codegen

# Main makefile suite - rules
include $(WORKSPACE_ROOT)/.workspace/rules.mk
