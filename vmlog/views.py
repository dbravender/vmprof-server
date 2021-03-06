# -*- coding: utf-8 -*-
import hashlib
import time
import os
import socket
from collections import defaultdict
from io import BytesIO

from django.core.exceptions import ObjectDoesNotExist
from django.conf.urls import url, include
from django.contrib import auth
from django.http.response import HttpResponseBadRequest
from django.http import Http404
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404

from vmlog.models import BinaryJitLog, get_reader
from vmlog.serializer import (VisualTraceTreeSerializer, LogMetaSerializer,
        TraceSerializer, BadRequest)
from vmprofile.models import Log
from jitlog.parser import _parse_jitlog
from jitlog.objects import MergePoint
from jitlog import constants as const


from rest_framework import views
from rest_framework.response import Response
from rest_framework.parsers import FileUploadParser
from rest_framework import permissions
from rest_framework.serializers import BaseSerializer
from rest_framework import viewsets

def compute_checksum(file_obj):
    hash = hashlib.md5()
    for chunk in file_obj.chunks():
        hash.update(chunk)
    return hash.hexdigest()

class BinaryJitLogFileUploadView(views.APIView):
    parser_classes = (FileUploadParser,)
    permission_classes = (permissions.AllowAny,)

    def post(self, request, profile, format=None):
        file_obj = request.FILES['file']

        filename = file_obj.name
        if not filename.endswith('.zip'):
            return HttpResponseBadRequest("must be gzip compressed")

        checksum = compute_checksum(file_obj)

        user = request.user if request.user.is_authenticated() else None

        if profile.strip() == "":
            profile_obj = None
        else:
            profile_obj = Log.objects.get(checksum=profile)
        log, _ = BinaryJitLog.objects.get_or_create(file=file_obj, checksum=checksum,
                                                    user=user, profile=profile_obj)

        return Response(log.checksum)

class JsonExceptionHandlerMixin(object):
    def handle_exception(self, exc):
        if isinstance(exc, Http404):
            code = 404
        elif isinstance(exc, BadRequest):
            code = 400
        else:
            code = 500
        msg = 'internal server error'
        if hasattr(exc, 'args') and len(exc.args) > 0:
            msg = exc.args[0]
        return JsonResponse({'code': code, 'message': msg}, status=code)

def json_serialize(response, cmd, **kwargs):
    filename = "cache.socket"
    command = cmd.format(**kwargs) + '\r\n'
    if os.path.exists(filename):
        client = socket.socket( socket.AF_UNIX, socket.SOCK_STREAM)
        client.connect(filename)
        client.send(command.encode('utf-8'))
        content = []
        while True:
            data = client.recv(4096)
            if data == b"":
                break
            response.write(data)
    else:
        # should never be hit in production!!
        from twisted.test import proto_helpers
        from forestcache.cache import CacheProtocol
        prot = CacheProtocol()
        prot.transport = proto_helpers.StringTransport()
        prot.lineReceived(command.encode('utf-8'))
        response.write(prot.transport.value().decode('utf-8'))


def _load_jitlog_model(request, checksum):
    try:
        objs = BinaryJitLog.objects.filter(checksum=checksum)
        if len(objs) == 0:
            raise Http404
        if len(objs) != 1:
            raise BadRequest("checksum has several jit logs")
        # authentication? we do not implement that yet?
        return objs[0]
    except ObjectDoesNotExist:
        raise Http404

    raise BadRequest

def json_response(content):
    return HttpResponse(content, content_type="application/json")

def meta(request, profile):
    jl = _load_jitlog_model(request, profile)

    response = HttpResponse(content_type="application/json")
    json_serialize(response, "meta {filename} {profile}",
                             filename=jl.file.path, profile=profile)
    return response

def trace(request, profile):
    jl = _load_jitlog_model(request, profile)

    if 'id' not in request.GET:
        raise Http404("mandatory GET parameter 'id' missing")
    uid = int(request.GET['id'])
    response = HttpResponse(content_type="application/json")
    json_serialize(response, "trace {filename} {profile} {uid}",
                             filename=jl.file.path, profile=profile, uid=uid)
    return response

def stitches(request, profile):
    jl = _load_jitlog_model(request, profile)

    if 'id' not in request.GET:
        raise Http404("mandatory GET parameter 'id' missing")
    uid = int(request.GET['id'])
    response = HttpResponse(content_type="application/json")
    json_serialize(response, "stitch {filename} {profile} {uid}",
                             filename=jl.file.path, profile=profile, uid=uid)
    return response


