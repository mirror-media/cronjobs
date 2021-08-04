from gql import gql, Client
from gql.transport.requests import RequestsHTTPTransport

import __main__
import argparse
import logging
import os
import sys
import yaml

'''
For scheduled_editor_choices to run, a CMS bot user is required for it to mutate Editor Choices.
'''


def get_updated_state(state: str = 'draft') -> str:
    states_waterfall = ['scheduled', 'published', 'draft']

    # update the state value according to the original state, only scheduled and published will not cause an exception
    # if state is not scheduled and published, then an exception happens and we do not update it
    try:
        return states_waterfall[states_waterfall.index(state) + 1]
    except:
        return state


def create_authenticated_client(config_graphql: dict):
    cms_graphql_endpoint = config_graphql['apiEndpoint']
    gql_transport = RequestsHTTPTransport(
        url=cms_graphql_endpoint,
        use_json=True,
        headers={
            "Content-type": "application/json",
        },
        verify=True,
        retries=3,
    )

    gql_client = Client(
        transport=gql_transport,
        fetch_schema_from_transport=False,
    )

    # Authenticate through GraphQL
    qgl_mutation_authenticate_get_token = '''
    mutation {
    authenticate: authenticateUserWithPassword(email: "%s", password: "%s") {
        token
    }
    }
    '''

    username = config_graphql['username']
    password = config_graphql['password']

    mutation = gql(qgl_mutation_authenticate_get_token %
                   (username, password))

    token = gql_client.execute(mutation)['authenticate']['token']

    print(f'{os.path.basename(__file__)} has authenticated as {username}')

    gql_transport_with_token = RequestsHTTPTransport(
        url=cms_graphql_endpoint,
        use_json=True,
        headers={
            "Content-type": "application/json",
            'Authorization': f'Bearer {token}'
        },
        verify=True,
        retries=3,
    )

    return Client(
        transport=gql_transport_with_token,
        fetch_schema_from_transport=False,
    )


def unauthenticate_graphql_user(gql_authenticated_client: Client, username: str):
    qgl_mutate_unauthenticate_user = '''
    mutation {
        unauthenticate: unauthenticateUser {
            success
        }
    }
    '''

    mutation = gql(qgl_mutate_unauthenticate_user)
    unauthenticate = gql_authenticated_client.execute(mutation)[
        'unauthenticate']

    if unauthenticate['success'] == True:
        print(f'{os.path.basename(__file__)} has unauthenticated as {username}')
    else:
        print(f'{username} failed to unauthenticate')


def update_multiple_states(client: Client, mutation_name: str, content: list):

    new_data_list = ['{id: "%s", data:{state: %s}}' % (
        data["id"], get_updated_state(data["state"])) for data in content]
    new_data_str = '[' + ','.join(new_data_list) + ']'

    print(f'{mutation_name} is going to update: {new_data_str}')

    qgl_mutate_editor_choices_template = '''
        mutation {
            %s(data: %s) {
                id
                state
            }
        }
    '''

    mutation = gql(
        qgl_mutate_editor_choices_template % (mutation_name, new_data_str)
    )

    updatedData = client.execute(mutation)

    print(f'{mutation_name} updated:{updatedData}')

    return updatedData


def rotate_and_update_states(client: Client):

    where_condition = '{OR: [{state: published}, {state: scheduled}]}'

    qgl_query_content_to_modify = '''
    {
        allEditorChoices(where: %s) {
            state
            id
        }
        allVideoEditorChoices(where: %s) {
            state
            id
        }
        allPromotionVideos(where: %s) {
            state
            id
        }
    }
    ''' % (where_condition, where_condition, where_condition)

    content = client.execute(gql(qgl_query_content_to_modify))

    editor_choices = content['allEditorChoices']
    if len(editor_choices) != 0:
        update_multiple_states(client, 'updateEditorChoices', editor_choices)
    else:
        print('There is nothing to be updated for EditorChoice')

    video_editor_choices = content['allVideoEditorChoices']
    if len(video_editor_choices) != 0:
        update_multiple_states(
            client, 'updateVideoEditorChoices', video_editor_choices)
    else:
        print('There is nothing to be updated for VideoEditorChoice')

    promotion_videos = content['allPromotionVideos']
    if len(promotion_videos) != 0:
        update_multiple_states(
            client, 'updatePromotionVideos', promotion_videos)
    else:
        print('There is nothing to be updated for PromotionVideo')


__GRAPHQL_CMS_CONFIG_KEY = 'graphqlCMS'


def main(config_graphql: dict = None):
    logger = logging.getLogger(__main__.__file__)
    logger.setLevel('INFO')

    authenticated_gql_client = create_authenticated_client(config_graphql)

    rotate_and_update_states(authenticated_gql_client)

    unauthenticate_graphql_user(
        authenticated_gql_client, config_graphql['username'])


logging.basicConfig()

if __name__ == '__main__':
    logger = logging.getLogger(__main__.__file__)
    logger.setLevel('INFO')
    logger.info(f'{__file__} is executing...')
    parser = argparse.ArgumentParser(
        description='Process configuration of importPosts')
    parser.add_argument('-g', '--config-graphql', dest=__GRAPHQL_CMS_CONFIG_KEY,
                        help='graphql config file for importPosts', metavar='FILE', type=str, required=True)

    args = parser.parse_args()

    with open(getattr(args, __GRAPHQL_CMS_CONFIG_KEY), 'r') as stream:
        config_graphql = yaml.safe_load(stream)

    main(config_graphql=config_graphql)

    logger.info('exiting...good bye...')
