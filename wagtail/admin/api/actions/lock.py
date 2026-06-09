from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.serializers import Serializer

from wagtail.log_actions import log

from .base import APIAction


class LockPageAPIAction(APIAction):
    """
    Lock a page so other editors cannot edit it. Idempotent: re-locking an
    already-locked page is a no-op (it does not change the original locker).
    """

    serializer = Serializer

    def execute(self, instance, data):
        if not instance.permissions_for_user(self.request.user).can_lock():
            raise PermissionDenied(
                "You do not have permission to lock this page."
            )

        if not instance.locked:
            instance.locked = True
            instance.locked_by = self.request.user
            instance.locked_at = timezone.now()
            instance.save(
                update_fields=["locked", "locked_by", "locked_at"],
            )
            log(instance=instance, action="wagtail.lock", user=self.request.user)

        serializer = self.view.get_serializer(instance)
        return Response(serializer.data, status=status.HTTP_200_OK)


class UnlockPageAPIAction(APIAction):
    """
    Unlock a page. Idempotent: unlocking an already-unlocked page is a no-op.
    """

    serializer = Serializer

    def execute(self, instance, data):
        if not instance.permissions_for_user(self.request.user).can_unlock():
            raise PermissionDenied(
                "You do not have permission to unlock this page."
            )

        if instance.locked:
            instance.locked = False
            instance.locked_by = None
            instance.locked_at = None
            instance.save(
                update_fields=["locked", "locked_by", "locked_at"],
            )
            log(instance=instance, action="wagtail.unlock", user=self.request.user)

        serializer = self.view.get_serializer(instance)
        return Response(serializer.data, status=status.HTTP_200_OK)
